"""The error taxonomy, and an injector that produces each class on demand.

This is the instrument. The defense ladder is graded by asking, for each error
class, what fraction of instances each rung rejects, so the classes have to be
defined precisely enough that "caught" is not a judgment call.

THE TWO FAMILIES, and the distinction the whole project rests on:

  STRUCTURAL  the payload is the wrong SHAPE. Unparseable, missing a required
              field, wrong type, outside an enum. A schema is designed to
              catch exactly these, and does.

  VALUE       the payload is the right shape and the wrong CONTENT. Every
              field is present, correctly typed, in range, and plausible. No
              schema can see it, because there is nothing structurally wrong
              to see. This is the failure that reaches production.

Within VALUE there is a further split that decides whether rung 4
(cross-field business rules) helps at all:

  INCONSISTENT  the wrong value contradicts another field (a total that no
                longer equals subtotal + tax, a due date before the order
                date). Arithmetic catches it.
  CONSISTENT    the wrong values agree with each other. Shift a line item
                and the subtotal, tax and total with it, and the document
                still reconciles exactly (moving the three totals alone does
                not: the line items stop summing to the subtotal).
                Only evidence outside the payload, meaning where on the page the
                number was read from, can catch it.

CONSISTENT_VALUE is the class the ladder is really about. If schema validation
caught it, the central claim of this repository would be false.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass

from .docs import TAX_RATE_BP, Document

# Error class -> family. Used by the grader to aggregate.
STRUCTURAL = "structural"
VALUE_INCONSISTENT = "value_inconsistent"
VALUE_CONSISTENT = "value_consistent"

FAMILIES = {
    "malformed_json": STRUCTURAL,
    "missing_required": STRUCTURAL,
    "wrong_type": STRUCTURAL,
    "out_of_enum": STRUCTURAL,
    "hallucinated_field": STRUCTURAL,
    "null_for_required": STRUCTURAL,
    "date_order_swapped": VALUE_INCONSISTENT,
    "total_broken": VALUE_INCONSISTENT,
    "quantity_row_swap": VALUE_INCONSISTENT,
    "misread_digit_reconciled": VALUE_CONSISTENT,
    "transposed_digits": VALUE_CONSISTENT,
    "vendor_near_miss": VALUE_CONSISTENT,
    "shifted_and_reconciled": VALUE_CONSISTENT,
    "sku_near_miss": VALUE_CONSISTENT,
}

ERROR_CLASSES = tuple(FAMILIES)


@dataclass(frozen=True)
class Corruption:
    """A model output, plus what was done to it."""

    payload: str                  # raw text as a model would emit it
    error_class: str | None       # None for the clean case
    family: str | None
    corrupted_fields: tuple[str, ...]


def clean(doc: Document) -> Corruption:
    return Corruption(json.dumps(doc.truth()), None, None, ())


def _dumps(obj) -> str:
    return json.dumps(obj)


def corrupt(doc: Document, error_class: str, rng: random.Random) -> Corruption:
    """Produce one instance of `error_class` for `doc`."""
    if error_class not in FAMILIES:
        raise ValueError(f"unknown error class {error_class!r}")
    t = copy.deepcopy(doc.truth())
    fam = FAMILIES[error_class]

    # -- structural --------------------------------------------------------
    if error_class == "malformed_json":
        # A trailing comma and an unclosed brace: the commonest shape of
        # broken model JSON.
        return Corruption(_dumps(t)[:-1] + ',', error_class, fam, ())

    if error_class == "missing_required":
        f = rng.choice(("po_number", "total_cents", "vendor"))
        t.pop(f)
        return Corruption(_dumps(t), error_class, fam, (f,))

    if error_class == "wrong_type":
        # Money as a formatted string instead of integer cents.
        t["total_cents"] = f"${t['total_cents'] / 100:,.2f}"
        return Corruption(_dumps(t), error_class, fam, ("total_cents",))

    if error_class == "out_of_enum":
        t["currency"] = rng.choice(("Dollars", "usd", "US$", "$"))
        return Corruption(_dumps(t), error_class, fam, ("currency",))

    if error_class == "hallucinated_field":
        t["purchase_order_status"] = rng.choice(("approved", "pending"))
        return Corruption(_dumps(t), error_class, fam, ())

    if error_class == "null_for_required":
        t["due_date"] = None
        return Corruption(_dumps(t), error_class, fam, ("due_date",))

    # -- value, inconsistent ----------------------------------------------
    if error_class == "date_order_swapped":
        t["order_date"], t["due_date"] = t["due_date"], t["order_date"]
        return Corruption(_dumps(t), error_class, fam,
                          ("order_date", "due_date"))

    if error_class == "total_broken":
        # Total no longer equals subtotal + tax. Arithmetic sees it.
        t["total_cents"] = t["total_cents"] + rng.choice((-1000, -100, 100, 1000))
        return Corruption(_dumps(t), error_class, fam, ("total_cents",))

    if error_class == "quantity_row_swap":
        # Swap two quantities between rows that differ. Swapping equal
        # quantities is a no-op that produces the truth and is then scored
        # forever as an error nothing caught; a silent, permanent bias
        # toward "the defenses do not work". Found by a test, not by review.
        rows = t["line_items"]
        pair = next(((a, b) for a in range(len(rows))
                     for b in range(a + 1, len(rows))
                     if rows[a]["quantity"] != rows[b]["quantity"]), None)
        if pair is None:
            return clean(doc)
        a, b = pair
        rows[a]["quantity"], rows[b]["quantity"] = (
            rows[b]["quantity"], rows[a]["quantity"])
        # amount left untouched, so amount != quantity * unit_price
        return Corruption(_dumps(t), error_class, fam,
                          (f"line_items[{a}].quantity",
                           f"line_items[{b}].quantity"))

    # -- value, consistent (the interesting ones) --------------------------
    if error_class == "misread_digit_reconciled":
        # Misread ONE digit of one unit price the way a scan actually gets
        # misread, 0 for 8, 5 for 6, 1 for 7, then recompute that row's
        # amount and the totals block from the misread value.
        #
        # This is the most realistic error in the taxonomy, because it is what
        # a careful model does: it reads a number, and it does the arithmetic
        # correctly on what it read. Every internal rule therefore passes. The
        # payload is wrong by a few cents to a few dollars and nothing inside
        # it can say so.
        #
        # The recomputation is what makes it consistent. Changing the amount
        # alone leaves quantity * unit_price != amount, which rung 4 rejects
        # instantly: the case then sits in the CONSISTENT family while
        # behaving like an INCONSISTENT one, and rung 4's catch rate looks
        # better than it is. That is why family membership is asserted by
        # tests/test_families.py rather than trusted.
        rows = t["line_items"]
        i = rng.randrange(len(rows))
        s = list(str(rows[i]["unit_price_cents"]))
        swaps = {"0": "8", "8": "0", "5": "6", "6": "5", "1": "7", "7": "1",
                 "3": "9", "9": "3", "2": "7", "4": "9"}
        pos = [k for k, ch in enumerate(s) if ch in swaps]
        if not pos:
            return clean(doc)
        k = rng.choice(pos)
        s[k] = swaps[s[k]]
        rows[i]["unit_price_cents"] = int("".join(s))
        rows[i]["amount_cents"] = rows[i]["quantity"] * rows[i]["unit_price_cents"]
        sub = sum(r["amount_cents"] for r in rows)
        tax = (sub * TAX_RATE_BP + 5000) // 10000
        t["subtotal_cents"], t["tax_cents"], t["total_cents"] = sub, tax, sub + tax
        return Corruption(_dumps(t), error_class, fam,
                          (f"line_items[{i}].unit_price_cents",
                           f"line_items[{i}].amount_cents", "subtotal_cents",
                           "tax_cents", "total_cents"))

    if error_class == "transposed_digits":
        # Transpose the last two adjacent digits that differ. "po-95000" ends
        # in two zeros; transposing those returns the truth, which would be
        # scored as an uncaught error. Same silent no-op as quantity_row_swap,
        # same test caught it.
        s = list(str(t["po_number"]))
        digits = [i for i, c in enumerate(s) if c.isdigit()]
        pair = next(((digits[k], digits[k + 1])
                     for k in range(len(digits) - 2, -1, -1)
                     if s[digits[k]] != s[digits[k + 1]]), None)
        if pair is None:
            return clean(doc)
        i, j = pair
        s[i], s[j] = s[j], s[i]
        return Corruption(_dumps({**t, "po_number": "".join(s)}),
                          error_class, fam, ("po_number",))

    if error_class == "vendor_near_miss":
        v = t["vendor"]
        variants = (v.replace(" Co", " Company"), v.replace("Co", "Corp"),
                    v + " Inc", v.replace("g", "q", 1))
        t["vendor"] = rng.choice([x for x in variants if x != v] or [v + " Ltd"])
        return Corruption(_dumps(t), error_class, fam, ("vendor",))

    if error_class == "shifted_and_reconciled":
        # The case that matters. Drop one line item entirely and recompute
        # subtotal, tax and total from what remains. The payload is complete,
        # correctly typed, in range, and every cross-field rule passes; the
        # arithmetic is internally perfect. It is still the wrong answer,
        # because a row that is on the page is missing from it.
        rows = t["line_items"]
        if len(rows) < 3:
            return clean(doc)
        dropped = rng.randrange(len(rows))
        del rows[dropped]
        for n, r in enumerate(rows, start=1):
            r["row"] = n
        sub = sum(r["amount_cents"] for r in rows)
        tax = (sub * TAX_RATE_BP + 5000) // 10000
        t["subtotal_cents"], t["tax_cents"], t["total_cents"] = sub, tax, sub + tax
        return Corruption(_dumps(t), error_class, fam,
                          ("line_items", "subtotal_cents", "tax_cents",
                           "total_cents"))

    if error_class == "sku_near_miss":
        # Misread ONE character of one part number, the way a low-resolution
        # scan actually does it: 0/O, 1/I, 5/S, 8/B, 2/Z.
        #
        # Added after the degraded-scan run, not before it. 15 of the 19 errors
        # a real model made at 108 DPI were exactly this, and the taxonomy had
        # no class for it, so the offline measurement could not have predicted
        # the most common real failure. That gap is the point: a constructed
        # taxonomy only covers the failures its author thought of, and the only
        # cure is to go and look at what actually happens.
        #
        # Every money field is untouched, so all of rungs 1-6 pass. Only rung 7,
        # which knows which part numbers exist, can reject it.
        rows = t["line_items"]
        i = rng.randrange(len(rows))
        swaps = {"0": "O", "O": "0", "1": "I", "I": "1", "5": "S", "S": "5",
                 "8": "B", "B": "8", "2": "Z", "Z": "2"}
        s = list(str(rows[i]["sku"]))
        pos = [k for k, ch in enumerate(s) if ch in swaps]
        if not pos:
            return clean(doc)
        k = rng.choice(pos)
        s[k] = swaps[s[k]]
        rows[i]["sku"] = "".join(s)
        return Corruption(_dumps(t), error_class, fam,
                          (f"line_items[{i}].sku",))

    raise AssertionError(f"unhandled error class {error_class!r}")
