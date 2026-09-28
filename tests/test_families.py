"""The tests that keep the central claim honest.

The whole repository asserts one thing: schema validation catches structural
errors and is blind to consistent-value errors. That claim is only as good as
the taxonomy behind it, and a misfiled error class would make the headline
result true by construction instead of true by measurement.

So the family labels are not documentation here; they are executable. Every
class is run through the ladder and required to behave the way its family
says. One class (confusable_unit_price) was already caught by exactly this
test sitting in the wrong family: it left quantity * unit_price != amount, so
rung 4 rejected it while it was labeled CONSISTENT, which would have made
rung 4 look better than it is.
"""

from __future__ import annotations

import json
import random

import pytest

from vei.docs import corpus, make_document
from vei.errors import (ERROR_CLASSES, FAMILIES, STRUCTURAL, VALUE_CONSISTENT,
                        VALUE_INCONSISTENT, clean, corrupt)
from vei.ladder import (GROUNDED_FIELDS, LADDER, RUNG_NAMES, rung_cross_field,
                        rung_grounded, rung_json,
                        rung_strict, rung_typed)
from vei.render import can_render

DOCS = [make_document(i) for i in range(40)]

# The taxonomy is pure standard library and is tested where nothing but pytest
# is installed, so the Pillow guard cannot go at module level the way it does
# in test_render.py. One test below draws real pages for their regions.
needs_rendering = pytest.mark.skipif(
    not can_render(),
    reason="needs Pillow and a TrueType font; the rendering CI job covers it")


def _corrupted(cls):
    """Every (doc, corruption) pair for `cls` where the corruption applied.

    Some classes need at least three line items and fall back to clean() on
    short documents; those are skipped rather than counted as misses.
    """
    rng = random.Random(f"test-{cls}")
    for d in DOCS:
        c = corrupt(d, cls, rng)
        if c.error_class is not None:
            yield d, c


def test_every_class_is_reachable():
    """No class may be silently unproducible on the standard corpus."""
    for cls in ERROR_CLASSES:
        got = list(_corrupted(cls))
        assert got, f"{cls} never produced a corruption over {len(DOCS)} docs"


@pytest.mark.parametrize("cls", [c for c in ERROR_CLASSES
                                 if FAMILIES[c] == STRUCTURAL])
def test_structural_errors_are_caught_by_the_schema(cls):
    """A structural error must be rejected at rung 3 or earlier.

    This is the half of the claim that is favorable to schemas, and it has to
    hold or the comparison is not fair to them.
    """
    for doc, c in _corrupted(cls):
        v = rung_strict(c.payload, doc, None)
        assert not v.accepted, (
            f"{cls} survived the strict schema: {c.payload[:120]}")


@pytest.mark.parametrize("cls", [c for c in ERROR_CLASSES
                                 if FAMILIES[c] == VALUE_INCONSISTENT])
def test_inconsistent_values_pass_the_schema_and_fail_arithmetic(cls):
    """The defining property of this family, both halves of it."""
    for doc, c in _corrupted(cls):
        assert rung_strict(c.payload, doc, None).accepted, (
            f"{cls} is labeled a VALUE error but the schema rejected it; "
            f"it is structural")
        assert not rung_cross_field(c.payload, doc, None).accepted, (
            f"{cls} is labeled INCONSISTENT but cross-field rules accepted it; "
            f"it is consistent")


@pytest.mark.parametrize("cls", [c for c in ERROR_CLASSES
                                 if FAMILIES[c] == VALUE_CONSISTENT])
def test_consistent_values_survive_every_payload_only_check(cls):
    """THE LOAD-BEARING TEST.

    A consistent-value error must survive rungs 1 through 4: parse, types,
    strict schema, and cross-field arithmetic. If any of them catches it, the
    class is misfiled and the headline result is inflated.
    """
    for doc, c in _corrupted(cls):
        for rung in (rung_json, rung_typed, rung_strict, rung_cross_field):
            v = rung(c.payload, doc, None)
            assert v.accepted, (
                f"{cls} is labeled CONSISTENT but {v.rung} rejected it "
                f"({v.reason}). Either the class or its family label is wrong.")


@pytest.mark.parametrize("cls", ERROR_CLASSES)
def test_corruption_actually_changes_the_payload(cls):
    """A no-op corruption would be scored as an uncaught error forever."""
    for doc, c in _corrupted(cls):
        assert c.payload != clean(doc).payload, f"{cls} produced the truth"


def test_clean_documents_pass_every_payload_rung():
    """False-rejection floor. If a rung rejects clean input, its catch rate is
    meaningless, so this is checked before any catch rate is believed."""
    for doc in DOCS:
        c = clean(doc)
        for rung in (rung_json, rung_typed, rung_strict, rung_cross_field):
            v = rung(c.payload, doc, None)
            assert v.accepted, f"rung {v.reason} rejected a clean document"


@needs_rendering
def test_the_false_rejection_floor_covers_every_rung_on_the_ladder():
    """The floor, over the whole ladder rather than a hand-written list.

    A rung that rejects everything has a perfect catch rate and is worthless,
    and catch rate alone hides that; this repository says so in its own README
    and prints a false-rejection column beside every catch rate for exactly
    that reason. A floor that names four rungs by hand does not protect the
    other four, and the two the headline rests on are among them: rungs 6 and
    7 are each touched by a single assertion elsewhere and both of those
    assert REJECTION, which a reject-always rung satisfies perfectly.

    Iterating LADDER rather than a list is the point. A rung added later is
    covered on the day it is added.
    """
    from vei.models import StubModel
    from vei.render import render

    stub = StubModel(seed=11)
    for doc in DOCS:
        render(doc)
        ex = stub.extract(doc)                 # no error_class: a clean read
        for name, rung in LADDER:
            v = rung(ex.payload, doc, ex.evidence)
            assert v.accepted, (
                f"rung {name} rejected a clean document ({v.reason}). Its "
                f"catch rate is not a measurement of anything.")


def test_the_rung_names_match_the_ladder_they_label():
    """Every artifact in audit/, every README column and regrade.py's output
    are keyed by these names. RUNG_NAMES and LADDER can be reordered
    independently, and a permutation mislabels every published catch rate
    without changing a single number, so the two orders are asserted equal
    instead of assumed to be maintained together."""
    assert RUNG_NAMES == tuple(name for name, _ in LADDER)
    assert len(RUNG_NAMES) == len(set(RUNG_NAMES)), "a rung name is duplicated"


def test_shifted_and_reconciled_is_arithmetically_perfect():
    """Spelled out explicitly because it is the example in the README.

    Every internal rule holds: rows multiply out, rows sum to subtotal, tax
    matches the rate, subtotal plus tax equals total. The document is simply
    missing a row that is printed on the page.
    """
    rng = random.Random("readme")
    doc = make_document(0)
    c = corrupt(doc, "shifted_and_reconciled", rng)
    obj = json.loads(c.payload)

    assert len(obj["line_items"]) == len(doc.items) - 1
    for r in obj["line_items"]:
        assert r["quantity"] * r["unit_price_cents"] == r["amount_cents"]
    rows_sum = sum(r["amount_cents"] for r in obj["line_items"])
    assert rows_sum == obj["subtotal_cents"]
    assert obj["subtotal_cents"] + obj["tax_cents"] == obj["total_cents"]
    assert rung_cross_field(c.payload, doc, None).accepted
    assert obj["total_cents"] != doc.total_cents          # and it is still wrong


def test_grounding_needs_the_row_count_to_catch_a_dropped_row():
    """Mutation check on rung 5.

    Rung 5 has two independent mechanisms: citation overlap and row count. A
    dropped row is caught only by the second. Removing row_count from the
    evidence must flip the verdict to accepted, if it does not, something
    else is catching the error and the rung's attribution is wrong.
    """
    rng = random.Random("mutation")
    doc = make_document(0)
    doc.regions.update({"po_number": (0, 0, 10, 10),
                        "total_cents": (0, 0, 10, 10),
                        "subtotal_cents": (0, 0, 10, 10)})
    cites = {f: [0, 0, 10, 10]
             for f in ("po_number", "total_cents", "subtotal_cents")}
    c = corrupt(doc, "shifted_and_reconciled", rng)

    with_count = rung_grounded(c.payload, doc,
                               {"citations": cites, "row_count": len(doc.items)})
    without = rung_grounded(c.payload, doc, {"citations": cites})

    assert not with_count.accepted, "row count did not catch the dropped row"
    assert without.accepted, (
        "rung 5 caught the dropped row without the row count -- some other "
        "check is doing the work and the finding is misattributed")


def test_corpus_makes_rows_two_and_three_confusable():
    """The corpus trap: rows 2 and 3 carry near-identical AMOUNTS.

    Amounts, not unit prices. Asserting the UNIT PRICES are close asserts
    something the generator never claimed and does not do, and the tempting
    response when that fails is to relax the threshold until it passes rather
    than to notice the assertion is the thing that is wrong.
    """
    close = total = 0
    for doc in corpus(60, confusable=True):
        if len(doc.items) >= 3:
            total += 1
            if abs(doc.items[1].amount_cents - doc.items[2].amount_cents) <= 40:
                close += 1
    assert total >= 50, f"only {total} documents had 3+ rows"
    assert close == total, f"only {close} of {total} documents had a close pair"


def test_control_corpus_has_no_confusable_pair():
    """The control arm. If confusable=False produced the same documents, the
    corpus would not be manipulating anything and the trap would be imaginary."""
    close = 0
    for doc in corpus(60, confusable=False):
        if len(doc.items) >= 3:
            if abs(doc.items[1].amount_cents - doc.items[2].amount_cents) <= 40:
                close += 1
    assert close <= 3, f"{close} control documents were confusable by accident"


@needs_rendering
def test_the_two_citation_assumptions_do_not_give_the_same_answer():
    """Rung 5's offline catch rate is a function of where a misread cites, so
    the stub states that assumption both ways. If these two ever collapse to
    the same number, either the stub stopped distinguishing them or rung 5
    stopped reading citations, and the published pair of columns would
    silently become one number.
    """
    from vei.models import StubModel
    from vei.render import render

    caught = {}
    for honest in (True, False):
        stub = StubModel(seed=5)
        hits = 0
        for doc in corpus(20):
            render(doc)
            ex = stub.extract(doc, error_class="misread_digit_reconciled",
                              honest_citations=honest)
            hits += not rung_grounded(ex.payload, doc, ex.evidence).accepted
        caught[honest] = hits
    assert caught[True] == 0, (
        "a citation on the true region cannot fail the citation check; if this "
        "fires, rung 5 is catching something else and the columns are mislabeled")
    assert caught[False] == 20


@needs_rendering
def test_the_stubs_row_count_comes_from_the_page_and_not_from_the_payload():
    """The one assumption rung 5 rests on.

    models.py says it outright: the row count must not come "from the same
    extraction being graded, which would make the check circular. That
    assumption is what rung 5 rests on." The row-count check is the only
    thing in the whole ladder that catches a dropped row whose arithmetic was
    recomputed to match, and it catches it by comparing the payload against a
    count taken from the PAGE. Sourced from the payload instead, the two
    always agree, the check can never fire, and rung 5's catch rate on the
    conservative column falls from every one of them to none.

    Asserted on a corruption that DROPS a row, because that is the case where
    an honest count and a circular one differ.
    """
    from vei.models import StubModel
    from vei.render import render

    stub = StubModel(seed=3)
    checked = 0
    for doc in corpus(10):
        render(doc)
        ex = stub.extract(doc, error_class="shifted_and_reconciled")
        payload_rows = len(json.loads(ex.payload)["line_items"])
        assert ex.evidence["row_count"] == len(doc.items), (
            "the evidence row count no longer comes from the document")
        assert ex.evidence["row_count"] != payload_rows, (
            "this corruption did not drop a row, so the test proves nothing")
        assert not rung_grounded(ex.payload, doc, ex.evidence).accepted
        checked += 1
    assert checked == 10


@needs_rendering
def test_rung_five_requires_a_citation_for_every_grounded_field():
    """A payload citing one field and staying silent on the rest must not pass
    grounding. Deleting the missing-citation branch leaves the overlap check
    in place, which only ever looks at fields that ARE cited, so a model that
    cites nothing at all sails through."""
    from vei.models import StubModel
    from vei.render import render

    doc = next(iter(corpus(1)))
    render(doc)
    ex = StubModel(seed=1).extract(doc)
    assert rung_grounded(ex.payload, doc, ex.evidence).accepted

    for dropped in GROUNDED_FIELDS:
        thin = dict(ex.evidence)
        thin["citations"] = {k: v for k, v in ex.evidence["citations"].items()
                             if k != dropped}
        v = rung_grounded(ex.payload, doc, thin)
        assert not v.accepted, f"grounding passed with no citation for {dropped}"
        assert dropped in v.reason
