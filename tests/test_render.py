"""Renderer tests. These guard the grounding ground truth.

Every grounding claim this repository makes rests on doc.regions being the
true pixel bounds of the printed value. If a region is wrong, rung 5 and rung
6 are measuring nothing and would still produce a confident-looking table.
"""

from __future__ import annotations

import io

import pytest

from vei import fonts
from vei.docs import cents, corpus, make_document
from vei.render import render, visual_tokens_for

pytest.importorskip("PIL")
pytestmark = pytest.mark.skipif(not fonts.available(),
                                reason="no TrueType font on this machine")


def _open(png: bytes):
    from PIL import Image
    return Image.open(io.BytesIO(png))


def test_every_value_field_gets_a_region():
    doc = make_document(0)
    render(doc)
    for f in ("po_number", "order_date", "due_date", "vendor", "terms",
              "currency", "subtotal_cents", "tax_cents", "total_cents"):
        assert f in doc.regions, f"no region recorded for {f}"
    for i in range(len(doc.items)):
        for cell in ("sku", "quantity", "unit_price_cents", "amount_cents"):
            assert f"line_items[{i}].{cell}" in doc.regions


def test_regions_are_inside_the_cropped_image():
    """The crop must not cut off anything a region points at.

    This is the specific way cropping goes wrong: the image shrinks, the
    recorded coordinates do not, and citations start pointing outside the
    picture the model was actually shown.
    """
    for doc in corpus(12):
        png = render(doc)
        with _open(png) as im:
            w, h = im.size
        for name, (x0, y0, x1, y1) in doc.regions.items():
            assert 0 <= x0 < x1 <= w, f"{name} x out of image: {(x0, x1)} vs {w}"
            assert 0 <= y0 < y1 <= h, f"{name} y out of image: {(y0, y1)} vs {h}"


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.parametrize("kind", ["loud", "consistent"])
def test_no_two_regions_overlap(kind):
    """No printed value may be drawn on top of another one.

    This test exists because the page failed it for a whole round of paid runs.
    The metadata_field injection replaces `terms` with a 160-character
    sentence; `currency` was then drawn at the middle of the same baseline, so
    USD landed on the injected subtotal digits, the exact value the injection
    asks the model to copy, and on the currency cell. Compliance rates were
    measured against a partly illegible ask and the artifacts recorded none of
    it, because they stored the verdict and not the page.

    Every other channel and every clean page passed this check the whole time,
    which is what makes it a cheap permanent guard rather than a special case.
    """
    from vei.inject import CHANNELS, render_with_injection

    for doc in corpus(10):
        render(doc)
        pages = [("clean", dict(doc.regions))]
        for channel in CHANNELS:
            doc.regions.clear()
            render_with_injection(doc, channel, kind=kind)
            pages.append((channel, dict(doc.regions)))
        for label, regions in pages:
            names = sorted(regions)
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    assert not _overlaps(regions[a], regions[b]), (
                        f"{doc.doc_id} {label} ({kind}): {a} {regions[a]} is "
                        f"drawn over {b} {regions[b]}")


def test_wrapping_never_loses_text():
    """Wrapping may move words to the next line; it may not drop them.

    A wrap that silently truncates would make the page stop saying what the
    ground truth says it says, and every grader in the repo would then be
    comparing an extraction against a value that was never printed.
    """
    from vei.render import _wrap

    text = " ".join(f"word{i}" for i in range(40))
    lines = _wrap(text, lambda s: len(s) * 10, 100)
    assert " ".join(lines).split() == text.split()
    assert all(lines)
    # One word wider than the limit gets its own line instead of vanishing.
    assert _wrap("supercalifragilistic", lambda s: len(s) * 10, 50) == \
        ["supercalifragilistic"]


def test_cropping_does_not_move_any_region():
    """Regions must be identical with and without the crop.

    If they differ, the crop is removing pixels from the top or left and every
    coordinate has shifted.
    """
    a, b = make_document(3), make_document(3)
    render(a, crop=True)
    render(b, crop=False)
    assert a.regions == b.regions


def test_regions_actually_contain_ink():
    """A region that points at blank paper is worse than no region at all:
    the citation check would pass on nothing."""
    doc = make_document(1)
    png = render(doc)
    with _open(png) as im:
        gray = im.convert("L")
        for name, (x0, y0, x1, y1) in doc.regions.items():
            crop = gray.crop((x0, y0, x1, y1))
            # getdata() is deprecated in Pillow 12 and removed in 14.
            darkest = crop.getextrema()[0]
            assert darkest < 200, f"region for {name} is blank paper"


def test_crop_is_a_real_saving():
    """The crop is justified in the code by a cost claim; check the claim."""
    doc = make_document(0)
    full, _, _ = visual_tokens_for(render(doc, crop=False))
    cropped, _, _ = visual_tokens_for(render(doc, crop=True))
    assert cropped < full / 2, (
        f"crop saved only {full - cropped} of {full} visual tokens; the "
        f"comment in render.py claims roughly 3x and should be corrected")


def test_confusable_rows_are_visibly_near_identical():
    """The trap has to survive rendering, not just exist in the data."""
    doc = make_document(0)
    assert len(doc.items) >= 3
    a = cents(doc.items[1].amount_cents)
    b = cents(doc.items[2].amount_cents)
    assert a != b, "the two amounts are identical, which is a different test"
    assert abs(doc.items[1].amount_cents - doc.items[2].amount_cents) <= 40


def test_render_is_deterministic():
    """Same document, same bytes. Otherwise corpus runs are not comparable."""
    assert render(make_document(7)) == render(make_document(7))


@pytest.mark.parametrize("kind", ["loud", "consistent"])
def test_every_drawn_injection_is_a_region_inside_the_page(kind):
    """The injected text is drawn after the page's regions are recorded, so it
    was invisible to the overlap test above: the consistent ask crossed the
    TOTAL row on three channels, and the rotated strip cut it off before its
    numbers. Each drawn injection now records its box, which must lie inside
    the image; test_no_two_regions_overlap then checks it against every value.
    """
    import io

    from PIL import Image

    from vei.inject import CHANNELS, INJECTED_REGION, METADATA, render_with_injection

    for doc in corpus(5):
        for channel in CHANNELS:
            if channel == METADATA:
                continue          # it replaces a field; its region is `terms`
            doc.regions.clear()
            png, _ = render_with_injection(doc, channel, kind=kind)
            w, h = Image.open(io.BytesIO(png)).size
            x0, y0, x1, y1 = doc.regions[INJECTED_REGION]
            assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, (
                f"{doc.doc_id} {channel} ({kind}): injected text at "
                f"{(x0, y0, x1, y1)} runs off a {w}x{h} page")


@pytest.mark.parametrize("kind", ["loud", "consistent"])
def test_a_page_does_not_depend_on_the_channels_drawn_before_it(kind):
    """inject_run.py draws every channel in turn on the same Document objects.
    The region left by one channel's injection must not move the next one's,
    so each page, and each injected box, equals the one drawn on a fresh
    document."""
    from vei.inject import CHANNELS, INJECTED_REGION, render_with_injection

    reused = list(corpus(5))
    for channel in CHANNELS:
        for i, doc in enumerate(reused):
            png, _ = render_with_injection(doc, channel, kind=kind)
            fresh = list(corpus(5))[i]
            want, _ = render_with_injection(fresh, channel, kind=kind)
            assert doc.regions.get(INJECTED_REGION) == \
                fresh.regions.get(INJECTED_REGION), (
                f"{doc.doc_id} {channel} ({kind}): injected box "
                f"{doc.regions.get(INJECTED_REGION)} after earlier channels, "
                f"{fresh.regions.get(INJECTED_REGION)} on a fresh document")
            assert png == want, f"{doc.doc_id} {channel} ({kind}): page differs"


def test_injection_lines_lose_no_word_and_fit_their_width():
    from PIL import ImageFont

    from vei.fonts import font_path
    from vei.inject import CONSISTENT, CHANNELS, injection_lines, make_injection

    font = ImageFont.truetype(font_path("sans"), 8)
    for channel in CHANNELS:
        text = make_injection(make_document(0), channel, CONSISTENT).text
        lines = injection_lines(text, font, 270)
        assert " ".join(lines).split() == text.split()
        assert all(font.getlength(line) <= 270 for line in lines
                   if " " in line), lines
