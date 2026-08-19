"""Synthetic purchase orders with known ground truth.

Why synthetic. Every field's correct value AND its pixel region are known by
construction, so a wrong extraction can be classified exactly. "read row 3
instead of row 2" is distinguishable from "misread a digit", which is what the
whole defense-ladder measurement turns on. A public document corpus gives you
neither the pixel regions nor a license to redistribute, and hand-labeling
introduces annotator disagreement into the ground truth you are measuring
against.

This module is pure standard library. The corpus, the ground truth and every
metric computed from them are testable without Pillow; only vei/render.py
needs it.

Money is integer cents throughout. A float total that reconciles to within
1e-9 is not a total that reconciles, and the cross-field rule at rung 4 has to
be exact or it is not a rule.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterator

# ---------------------------------------------------------------------------
# Vocabulary. Deliberately mundane: the interesting variation is structural
# (how many rows, how confusable the amounts), not lexical.
# ---------------------------------------------------------------------------

_VENDORS = (
    "Ridgeline Supply Co", "Cascade Industrial", "Fairmont Materials",
    "Northgate Trading", "Ashcroft Components", "Lakeside Fabrication",
    "Brightwater Tooling", "Kingsford Metals",
)
_PARTS = (
    ("Hex bolt M10x40, zinc", "HB-1040Z"),
    ("Flat washer M10, stainless", "FW-10SS"),
    ("Nylon lock nut M10", "LN-10NY"),
    ("Anchor bolt 1/2in x 6in", "AB-0506"),
    ("Threaded rod 3/8in, 6ft", "TR-0372"),
    ("Structural screw #14 x 3in", "SS-1403"),
    ("Joist hanger, 2x8", "JH-0208"),
    ("Post base, 4x4 adjustable", "PB-0404"),
    ("Concrete sleeve anchor 3/8", "CS-0375"),
    ("Self-drilling screw #12", "SD-1200"),
)
_TERMS = ("Net 30", "Net 45", "Net 60", "Due on receipt", "2/10 Net 30")

# The parts master. Every SKU that may legitimately appear on a document.
#
# This is not a convenience for the tests; it is the external reference data
# that rung 7 checks against, the one piece of the ladder that is realistic
# BECAUSE it is boring. Any procurement system already has a parts master; a
# permitting system has a jurisdiction's fee schedule; a claims system has a
# procedure code set. The check is only interesting because the degraded run
# showed the ladder had no rung aimed at the field that actually moved, and
# this is the cheapest possible thing that is.
CATALOG_SKUS = frozenset(sku for _, sku in _PARTS)

TAX_RATE_BP = 825  # 8.25%, in basis points. Integer math only.


@dataclass(frozen=True)
class LineItem:
    """One table row. `row` is 1-indexed as printed on the page."""

    row: int
    sku: str
    description: str
    quantity: int
    unit_price_cents: int

    @property
    def amount_cents(self) -> int:
        return self.quantity * self.unit_price_cents


@dataclass(frozen=True)
class Document:
    """A purchase order plus everything needed to grade an extraction of it."""

    doc_id: str
    po_number: str
    order_date: str      # ISO 8601
    due_date: str        # ISO 8601, strictly after order_date
    vendor: str
    terms: str
    currency: str
    items: tuple[LineItem, ...]
    # Populated by the renderer; empty until then. name -> (x0, y0, x1, y1).
    regions: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)

    # -- derived money -----------------------------------------------------
    @property
    def subtotal_cents(self) -> int:
        return sum(i.amount_cents for i in self.items)

    @property
    def tax_cents(self) -> int:
        # Round half up on the integer, so the printed total always reconciles.
        return (self.subtotal_cents * TAX_RATE_BP + 5000) // 10000

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents + self.tax_cents

    def truth(self) -> dict[str, object]:
        """The correct extraction, in the shape the schemas expect."""
        return {
            "po_number": self.po_number,
            "order_date": self.order_date,
            "due_date": self.due_date,
            "vendor": self.vendor,
            "terms": self.terms,
            "currency": self.currency,
            "subtotal_cents": self.subtotal_cents,
            "tax_cents": self.tax_cents,
            "total_cents": self.total_cents,
            "line_items": [
                {
                    "row": i.row,
                    "sku": i.sku,
                    "description": i.description,
                    "quantity": i.quantity,
                    "unit_price_cents": i.unit_price_cents,
                    "amount_cents": i.amount_cents,
                }
                for i in self.items
            ],
        }

    # Field names a grader compares one-for-one. Line items are graded
    # separately because they are a list, not a scalar.
    SCALAR_FIELDS = (
        "po_number", "order_date", "due_date", "vendor",
        "terms", "currency", "subtotal_cents", "tax_cents", "total_cents",
    )


def _iso(rng: random.Random) -> tuple[str, str]:
    """An order date and a due date strictly after it."""
    y = rng.choice((2025, 2026))
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    order = f"{y:04d}-{m:02d}-{d:02d}"
    # Due date lands in a later month, so string comparison is enough and the
    # rung-4 ordering rule needs no date library.
    dm = m + rng.randint(1, 3)
    dy = y + (dm - 1) // 12
    dm = (dm - 1) % 12 + 1
    return order, f"{dy:04d}-{dm:02d}-{d:02d}"


def make_document(index: int, *, confusable: bool = True) -> Document:
    """Build document `index`. Deterministic: same index, same document.

    `confusable` controls the point of the corpus. When True, at least two
    rows carry amounts within a few cents of one another, so a model that
    reads the wrong row produces a value that is well-formed, in range, and
    wrong, which is the failure this project exists to measure. Set False
    for a control corpus where every row is trivially distinguishable.
    """
    rng = random.Random(f"vei-doc-{index}")
    order_date, due_date = _iso(rng)
    n_rows = rng.randint(3, 6)
    parts = rng.sample(_PARTS, n_rows)

    items: list[LineItem] = []
    for r, (desc, sku) in enumerate(parts, start=1):
        qty = rng.randint(2, 40)
        unit = rng.randint(85, 9600)  # cents
        items.append(LineItem(r, sku, desc, qty, unit))

    if confusable and n_rows >= 3:
        # Make rows 2 and 3 carry near-identical AMOUNTS. This is the whole
        # trap: both are valid currency values in a plausible range, so a
        # model that reads the wrong row is not obviously wrong.
        #
        # The unit price must stay an integer number of cents, so row 3's
        # amount can only land on a multiple of its quantity. Snapping to the
        # NEAREST such multiple bounds the gap at quantity/2 cents: at most 20
        # here. Offsetting by a random +/- 40 first and then rounding stacks
        # rounding error on top of the offset and leaves 4 documents in 60
        # outside the window this docstring promises.
        q3 = items[2].quantity
        unit3 = max(1, round(items[1].amount_cents / q3))
        items[2] = LineItem(items[2].row, items[2].sku, items[2].description,
                            q3, unit3)

    return Document(
        doc_id=f"PO{index:05d}",
        po_number=f"PO-{rng.randint(10000, 99999)}",
        order_date=order_date,
        due_date=due_date,
        vendor=rng.choice(_VENDORS),
        terms=rng.choice(_TERMS),
        currency="USD",
        items=tuple(items),
    )


def corpus(n: int, *, confusable: bool = True) -> Iterator[Document]:
    """`n` deterministic documents."""
    for i in range(n):
        yield make_document(i, confusable=confusable)


def cents(v: int) -> str:
    """Format integer cents for printing. Never used for arithmetic."""
    sign = "-" if v < 0 else ""
    v = abs(v)
    return f"{sign}{v // 100:,}.{v % 100:02d}"
