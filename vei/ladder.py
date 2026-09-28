"""The defense ladder: eight rungs, each a gate over a model's raw output.

Each rung is `(name, fn)` where fn(payload_text, doc, evidence) returns a
Verdict. A rung ACCEPTS or REJECTS; it never repairs. Repair would confound
the measurement, because a rung that silently fixes an error looks identical
to a rung that never saw one.

The rungs are cumulative: rung N applies every check from rungs 0..N. That
is deliberate and it is how the defenses are actually deployed: nobody ships
cross-field rules without also parsing the JSON first. It also means the
catch-rate curve is monotonic by construction, so any flat segment in the
results is a real finding about that rung and not an artifact of ordering.

WHAT `evidence` IS. Rungs 0-4 ignore it: they see only the payload. Rung 5
(grounding) and rung 6 (targeted re-read) require the model to have cited a
source region per field, and check that citation against the document's known
regions. Rung 7 consults a third kind of input again, the parts master, and
is the only rung that can see a misread SKU.

Rung number is not cost order. Rungs are numbered in the order they were
added. Rung 7 needs only a catalog lookup, so it is cheaper than rungs 5 and 6
and would be deployed before them; it is last because renumbering would
invalidate every result already recorded in audit/.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .docs import CATALOG_SKUS, TAX_RATE_BP, Document

RUNG_NAMES = (
    "0_bare",
    "1_json_mode",
    "2_typed",
    "3_strict_schema",
    "4_cross_field",
    "5_grounded",
    "6_reread",
    "7_catalog",
)

# Fields a grounded extraction must cite. Line-item cells are cited by
# "line_items[i].amount_cents" style keys.
GROUNDED_FIELDS = ("po_number", "total_cents", "subtotal_cents")

_ALLOWED_TOP = {
    "po_number", "order_date", "due_date", "vendor", "terms", "currency",
    "subtotal_cents", "tax_cents", "total_cents", "line_items",
}
_REQUIRED_TOP = _ALLOWED_TOP - {"terms"}
_INT_FIELDS = ("subtotal_cents", "tax_cents", "total_cents")
# Typed as strings at rung 2. `currency` is left to rung 3's enum, so a null
# currency is rejected where it always was.
_STR_FIELDS = ("po_number", "vendor", "order_date", "due_date")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    rung: str
    reason: str = ""


def _ok(rung: str) -> Verdict:
    return Verdict(True, rung)


def _no(rung: str, reason: str) -> Verdict:
    return Verdict(False, rung, reason)


# ---------------------------------------------------------------------------
# Rung 0: no gate at all. The baseline. Accepts anything, including text
# that is not JSON. Present so the other rungs have something to improve on.
# ---------------------------------------------------------------------------
def rung_bare(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    return _ok("0_bare")


# ---------------------------------------------------------------------------
# Rung 1: JSON mode. Parses, nothing more. This is what "we turned on JSON
# mode" buys you, measured here precisely because teams treat it as a
# correctness control.
# ---------------------------------------------------------------------------
def rung_json(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    try:
        json.loads(payload)
    except json.JSONDecodeError as e:
        return _no("1_json_mode", f"unparseable: {e.msg}")
    return _ok("1_json_mode")


# ---------------------------------------------------------------------------
# Rung 2: a typed model. Required fields present, primitives correctly
# typed. Equivalent to a permissive Pydantic model.
# ---------------------------------------------------------------------------
def rung_typed(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    v = rung_json(payload, doc, evidence)
    if not v.accepted:
        return _no("2_typed", v.reason)
    obj = json.loads(payload)
    if not isinstance(obj, dict):
        return _no("2_typed", "top level is not an object")
    missing = sorted(_REQUIRED_TOP - set(obj))
    if missing:
        return _no("2_typed", f"missing required: {', '.join(missing)}")
    for f in _INT_FIELDS:
        if not isinstance(obj[f], int) or isinstance(obj[f], bool):
            return _no("2_typed", f"{f} is {type(obj[f]).__name__}, want int")
    if obj.get("due_date") is None or obj.get("order_date") is None:
        return _no("2_typed", "null in a required date")
    for f in _STR_FIELDS:
        if not isinstance(obj[f], str):
            return _no("2_typed", f"{f} is {type(obj[f]).__name__}, want str")
    if not isinstance(obj.get("line_items"), list) or not obj["line_items"]:
        return _no("2_typed", "line_items missing or empty")
    return _ok("2_typed")


# ---------------------------------------------------------------------------
# Rung 3: strict schema. Adds enums, ranges, and additionalProperties=false.
# This is the strongest gate that looks only at the payload's shape.
# ---------------------------------------------------------------------------
def rung_strict(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    v = rung_typed(payload, doc, evidence)
    if not v.accepted:
        return _no("3_strict_schema", v.reason)
    obj = json.loads(payload)
    extra = sorted(set(obj) - _ALLOWED_TOP)
    if extra:
        return _no("3_strict_schema", f"unexpected field(s): {', '.join(extra)}")
    if obj["currency"] != "USD":
        return _no("3_strict_schema", f"currency {obj['currency']!r} not in enum")
    for f in ("order_date", "due_date"):
        if not _ISO_DATE.fullmatch(obj[f]):
            return _no("3_strict_schema", f"{f} {obj[f]!r} is not YYYY-MM-DD")
    for f in _INT_FIELDS:
        if not (0 <= obj[f] <= 100_000_000):
            return _no("3_strict_schema", f"{f} out of range")
    for i, r in enumerate(obj["line_items"]):
        if not isinstance(r, dict):
            return _no("3_strict_schema", f"line_items[{i}] not an object")
        for k in ("row", "quantity", "unit_price_cents", "amount_cents"):
            if not isinstance(r.get(k), int) or isinstance(r.get(k), bool):
                return _no("3_strict_schema", f"line_items[{i}].{k} not int")
        if r["quantity"] <= 0:
            return _no("3_strict_schema", f"line_items[{i}].quantity <= 0")
    return _ok("3_strict_schema")


# ---------------------------------------------------------------------------
# Rung 4: cross-field business rules. Arithmetic and ordering that the
# payload must satisfy internally. This is where teams stop, the last rung
# that needs nothing but the payload.
# ---------------------------------------------------------------------------
def rung_cross_field(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    v = rung_strict(payload, doc, evidence)
    if not v.accepted:
        return _no("4_cross_field", v.reason)
    obj = json.loads(payload)
    if obj["order_date"] >= obj["due_date"]:
        return _no("4_cross_field", "due_date not after order_date")
    for i, r in enumerate(obj["line_items"]):
        if r["quantity"] * r["unit_price_cents"] != r["amount_cents"]:
            return _no("4_cross_field",
                       f"line_items[{i}]: qty * unit != amount")
    sub = sum(r["amount_cents"] for r in obj["line_items"])
    if sub != obj["subtotal_cents"]:
        return _no("4_cross_field", "line items do not sum to subtotal")
    tax = (sub * TAX_RATE_BP + 5000) // 10000
    if tax != obj["tax_cents"]:
        return _no("4_cross_field", "tax does not match rate on subtotal")
    if obj["subtotal_cents"] + obj["tax_cents"] != obj["total_cents"]:
        return _no("4_cross_field", "subtotal + tax != total")
    return _ok("4_cross_field")


# ---------------------------------------------------------------------------
# Rung 5: grounding. The extraction must cite a source region for each
# grounded field, and the cited region must overlap the region the value was
# actually printed in. This is the first rung that consults anything outside
# the payload, and the first that can see a consistent-but-wrong answer.
#
# Row count is part of the ground truth. A dropped row leaves the remaining
# arithmetic perfect, so the only way to notice is to compare the number of
# cited rows against the number of rows on the page.
# ---------------------------------------------------------------------------
def _overlaps(a, b) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


def _is_box(value) -> bool:
    """Four numbers. A model can return anything in a citation slot, and a
    malformed one is a rejection, not an exception that ends a paid run."""
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(isinstance(n, (int, float)) and not isinstance(n, bool)
                    for n in value))


def rung_grounded(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    v = rung_cross_field(payload, doc, evidence)
    if not v.accepted:
        return _no("5_grounded", v.reason)
    if not evidence or "citations" not in evidence:
        return _no("5_grounded", "no citations supplied")
    obj = json.loads(payload)
    cites = evidence["citations"]
    if not isinstance(cites, dict):
        return _no("5_grounded", "citations are not a field -> box mapping")

    # The row-count check. Cheap, and the only thing in the ladder that
    # catches a dropped row whose arithmetic was recomputed.
    if "row_count" in evidence and evidence["row_count"] != len(obj["line_items"]):
        return _no("5_grounded",
                   f"cited {evidence['row_count']} rows on the page, "
                   f"payload has {len(obj['line_items'])}")

    for f in GROUNDED_FIELDS:
        if f not in cites:
            return _no("5_grounded", f"no citation for {f}")
        if not _is_box(cites[f]):
            return _no("5_grounded", f"citation for {f} is not a box")
        truth_region = doc.regions.get(f)
        if truth_region is None:
            continue  # unrendered document; nothing to check against
        if not _overlaps(tuple(cites[f]), truth_region):
            return _no("5_grounded", f"citation for {f} is off the true region")
    return _ok("5_grounded")


# ---------------------------------------------------------------------------
# Rung 6: targeted re-read. Re-extracts only the cited regions and requires
# the two reads to agree. Costs a second call on a crop, not a whole page.
# `evidence["reread"]` maps field -> value from that second pass.
# ---------------------------------------------------------------------------
def _resolve(obj, path: str):
    """Look up "vendor" or "line_items[1].unit_price_cents" in a payload.

    Returns _MISSING when the path does not exist, which is NOT a disagreement:
    a re-read of a cell the payload never claimed has nothing to contradict.
    Rung 5 is what notices a row that went missing.
    """
    cur = obj
    for part in path.split("."):
        name, _, idx = part.partition("[")
        if name:
            if not isinstance(cur, dict) or name not in cur:
                return _MISSING
            cur = cur[name]
        if idx:
            try:
                i = int(idx.rstrip("]"))
            except ValueError:
                return _MISSING
            if not isinstance(cur, list) or not -len(cur) <= i < len(cur):
                return _MISSING
            cur = cur[i]
    return cur


_MISSING = object()


def rung_reread(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    v = rung_grounded(payload, doc, evidence)
    if not v.accepted:
        return _no("6_reread", v.reason)
    obj = json.loads(payload)
    reread = (evidence or {}).get("reread") or {}
    for f, second in reread.items():
        first = _resolve(obj, f)
        if first is _MISSING:
            continue
        if first != second:
            return _no("6_reread", f"{f}: first read {first!r}, "
                                   f"second read {second!r}")
    return _ok("6_reread")


# ---------------------------------------------------------------------------
# Rung 7: catalog membership. Every SKU on the payload must exist in the
# parts master.
#
# This rung was added because the measurement demanded it, not because the
# design anticipated it. The degraded-scan run found that 15 of 19 errors at
# the moderate level were misread part numbers, and that rungs 0-6 caught
# almost none of them: grounding verifies where a TOTAL was read from and has
# nothing to say about a SKU. The ladder had no rung aimed at the field that
# actually moved.
#
# Its number is chronological, not a cost order. Rung 7 needs a parts master
# and nothing else, no page, no second model call, so it is CHEAPER than
# rungs 5 and 6 and in production would be deployed before them. It is
# numbered last because renumbering would invalidate every result already
# recorded in audit/. Read the ladder as "what each rung requires", not as a
# recommended deployment sequence; the README says the same.
#
# The catalog is external reference data, which is a different kind of input
# from either the payload or the page. That is exactly why it can see this
# error: a misread SKU is perfectly well-formed and perfectly consistent, and
# only something that knows which part numbers EXIST can reject it.
# ---------------------------------------------------------------------------
def check_catalog(payload: str) -> Verdict:
    """The catalog check ALONE: no lower rungs, no page, no second call.

    Separated from rung_catalog because the two make different claims and
    conflating them made one of them false. `rung_catalog` is the cumulative
    ladder position and therefore inherits rung 5's requirement for citations;
    called with evidence=None it rejects everything, which would have made a
    test of "does the catalog check catch a misread SKU?" pass for entirely
    the wrong reason.

    THIS function is the one that is cheap: give it a payload and a parts
    master and it answers. That is what justifies deploying it before rungs 5
    and 6 despite its number.
    """
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as e:
        return _no("catalog", f"unparseable: {e.msg}")
    rows = obj.get("line_items")
    if not isinstance(rows, list):
        return _no("catalog", "line_items missing or not a list")
    for i, r in enumerate(rows):
        sku = r.get("sku") if isinstance(r, dict) else None
        if sku not in CATALOG_SKUS:
            return _no("catalog",
                       f"line_items[{i}].sku {sku!r} is not in the parts master")
    return _ok("catalog")


def rung_catalog(payload: str, doc: Document, evidence: dict | None) -> Verdict:
    v = rung_reread(payload, doc, evidence)
    if not v.accepted:
        return _no("7_catalog", v.reason)
    c = check_catalog(payload)
    return _ok("7_catalog") if c.accepted else _no("7_catalog", c.reason)


LADDER: tuple[tuple[str, Callable[[str, Document, dict | None], Verdict]], ...] = (
    ("0_bare", rung_bare),
    ("1_json_mode", rung_json),
    ("2_typed", rung_typed),
    ("3_strict_schema", rung_strict),
    ("4_cross_field", rung_cross_field),
    ("5_grounded", rung_grounded),
    ("6_reread", rung_reread),
    ("7_catalog", rung_catalog),
)
