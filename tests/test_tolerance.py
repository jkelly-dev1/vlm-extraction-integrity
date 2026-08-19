"""The re-scoring analysis has to be right, or it argues for the wrong rung.

scripts/tolerance_sweep.py exists to answer "should rung 5 be less strict?",
and a bug in its geometry would answer it confidently and wrongly. These tests
pin the two rules and one property of the sweep itself.
"""

from __future__ import annotations

import pytest

from scripts.tolerance_sweep import gap, nearest_rejects, worst_gap
from vei.render import can_render

# Most of this file is pure arithmetic and must keep running where nothing but
# pytest is installed, so the Pillow guard cannot go at module level the way it
# does in test_render.py. Two tests below draw a real page; they are the only
# ones here that need it.
needs_rendering = pytest.mark.skipif(
    not can_render(),
    reason="needs Pillow and a TrueType font; the rendering CI job covers it")

# A page fragment in the shape the renderer produces: a value, the value on the
# next line 6 px below it, and something far away.
SUB = (600, 100, 640, 108)
TAX = (600, 114, 640, 122)
FAR = (72, 300, 300, 320)
REGIONS = {"subtotal_cents": SUB, "tax_cents": TAX, "vendor": FAR}


def test_gap_is_zero_when_boxes_touch_and_a_distance_when_they_do_not():
    assert gap(SUB, SUB) == 0
    assert gap((595, 95, 605, 105), SUB) == 0        # overlapping corner
    assert gap(SUB, TAX) == 6                        # the row below
    # 300 px apart horizontally and 192 vertically. The rule is the LARGER of
    # the two, because growing a region by k pixels closes k on both axes at
    # once, so "would touch at tolerance k" means both gaps are within k.
    assert gap(SUB, FAR) == 300
    # Symmetric, or the answer depends on argument order and the sweep is
    # measuring something different in each direction.
    assert gap(SUB, TAX) == gap(TAX, SUB)


def _rec(**cites):
    return {"evidence": {"citations": cites}}


def test_worst_gap_reports_the_largest_miss_not_the_first():
    rec = _rec(po_number=list(FAR), subtotal_cents=list(SUB),
               total_cents=list(TAX))
    regions = {"po_number": FAR, "subtotal_cents": SUB, "total_cents": TAX}
    assert worst_gap(rec, regions) == 0
    # One field off by 6, another exact: the answer is 6, not 0.
    rec = _rec(po_number=list(FAR), subtotal_cents=list(TAX),
               total_cents=list(TAX))
    assert worst_gap(rec, regions) == 6


def test_worst_gap_is_none_when_a_citation_is_missing():
    """Absent is not the same as far away. The rung rejects a missing citation
    for its own reason, and folding that into a distance would let a tolerance
    sweep appear to 'fix' replies that never cited anything."""
    regions = {"po_number": FAR, "subtotal_cents": SUB, "total_cents": TAX}
    assert worst_gap(_rec(po_number=list(FAR)), regions) is None


def test_nearest_accepts_a_near_miss_and_rejects_the_neighboring_row():
    regions = dict(REGIONS, po_number=FAR, total_cents=(600, 128, 640, 136))
    fields = {"po_number": list(FAR), "total_cents": [600, 128, 640, 136]}

    # Sitting 2 px above its own value, it is still nearer to that than
    # to anything else.
    near_miss = _rec(subtotal_cents=[600, 92, 640, 98], **fields)
    assert not nearest_rejects(near_miss, regions)

    # Pointing at the tax row while claiming to cite the subtotal. A 6 px
    # tolerance accepts this; the nearest rule is what refuses it, and this is
    # the confusable-row failure the whole repository is about.
    wrong_row = _rec(subtotal_cents=list(TAX), **fields)
    assert nearest_rejects(wrong_row, regions)


def test_false_rejections_cannot_rise_as_tolerance_grows():
    """Monotonicity of the sweep. If growing the tolerance ever rejected MORE,
    the sweep's arithmetic is inverted somewhere and every conclusion drawn
    from it is backwards."""
    regions = {"po_number": FAR, "subtotal_cents": SUB, "total_cents": TAX}
    records = [_rec(po_number=[72, 290, 300, 296], subtotal_cents=list(SUB),
                    total_cents=list(TAX)),
               _rec(po_number=list(FAR), subtotal_cents=[600, 60, 640, 68],
                    total_cents=list(TAX))]
    gaps = [worst_gap(r, regions) for r in records]
    prev = len(records)
    for k in (0, 2, 4, 8, 16, 32, 64):
        rejected = sum(1 for g in gaps if g is None or g > k)
        assert rejected <= prev
        prev = rejected
    assert prev == 0


@needs_rendering
def test_adjacent_citations_land_near_the_value_and_far_ones_do_not():
    """The two citation models must actually differ in the way the analysis
    claims, or the constructed adjacent-row case measures nothing."""
    from vei.docs import make_document
    from vei.ladder import GROUNDED_FIELDS
    from vei.models import ADJACENT, FAR, StubModel
    from vei.render import render

    for mode, lo, hi in ((ADJACENT, 1, 40), (FAR, 100, 400)):
        doc = make_document(0)
        render(doc)
        ex = StubModel(seed=1, citation_miss=mode).extract(
            doc, error_class="shifted_and_reconciled", honest_citations=False)
        cites = ex.evidence["citations"]
        missed = [gap(tuple(cites[f]), doc.regions[f]) for f in GROUNDED_FIELDS
                  if gap(tuple(cites[f]), doc.regions[f]) > 0]
        assert missed, f"{mode} produced no misplaced citation at all"
        assert lo <= min(missed) <= hi, f"{mode} misses by {missed}"


def test_can_render_is_false_without_pillow_even_where_fonts_exist(monkeypatch):
    """The guard that keeps the offline half offline, pinned.

    A real failure, not a hypothetical: this repository's first CI run went red
    because three tests, two here, one in test_families.py, called render() in
    the pytest-only job, and scripts/offline_demo.py asked fonts.available()
    alone. That is True on a machine with fonts and no Pillow, one line before
    the import raises. Anything answering "can I draw" must ask both halves.

    Fonts are NOT faked away here: Pillow alone decides the answer on a
    machine where the font check would have said yes.
    """
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name, *a, **kw: None if name == "PIL" else real(name, *a, **kw))
    assert can_render() is False


def test_an_unknown_citation_model_is_refused():
    from vei.models import StubModel
    import pytest as _pytest
    with _pytest.raises(ValueError):
        StubModel(citation_miss="somewhere_vaguely_right")


@needs_rendering
def test_the_adjacent_case_is_actually_caught_at_zero_tolerance():
    """Guards the degenerate version of the constructed case: if the stub's
    adjacent citations happened to overlap the true region, the whole 'a
    tolerance of 8 px blinds rung 5' result would be measuring nothing."""
    from vei.docs import make_document
    from vei.ladder import LADDER
    from vei.models import ADJACENT, StubModel
    from vei.render import render

    rung5 = dict(LADDER)["5_grounded"]
    doc = make_document(2)
    render(doc)
    ex = StubModel(seed=1, citation_miss=ADJACENT).extract(
        doc, error_class="misread_digit_reconciled", honest_citations=False)
    assert not rung5(ex.payload, doc, ex.evidence).accepted
