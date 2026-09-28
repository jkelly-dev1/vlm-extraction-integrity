"""Degradation tests; these guard the ground truth through an affine.

Rescaling and rotation move every glyph. If the recorded regions do not move
with them, every grounding measurement on a degraded page is graded against
coordinates from a different image, and it still produces a confident table.
That is the failure these tests exist to prevent, and the only test that
really settles it is the one that looks at pixels.
"""

from __future__ import annotations

import io

import pytest

from vei import fonts
from vei.degrade import (CLEAN, HEAVY, LEVEL_NAMES, LEVELS, LIGHT, MODERATE,
                         degrade, effective_dpi)
from vei.docs import make_document
from vei.render import render

pytest.importorskip("PIL")
pytestmark = pytest.mark.skipif(not fonts.available(),
                                reason="no TrueType font on this machine")


def _img(png):
    from PIL import Image
    return Image.open(io.BytesIO(png)).convert("RGB")


def _rendered(i=0):
    doc = make_document(i)
    png = render(doc)
    return doc, png


@pytest.mark.parametrize("level", LEVEL_NAMES)
def test_regions_still_contain_ink(level):
    """THE LOAD-BEARING TEST.

    After degradation every region must still sit on top of dark pixels. This
    is what catches a wrong rotation sign, a missed rescale, or an off-by-one
    in the expand. None of which are visible in the arithmetic, and all of
    which produce regions that look perfectly plausible.

    Ink is measured relative to the page's own paper brightness, so the same
    criterion works at every level: heavy degradation genuinely washes the ink
    out, and an absolute threshold could not tell that apart from a box in the
    wrong place.
    """
    doc, png = _rendered(0)
    out, regions = degrade(png, level, seed=1, regions=doc.regions)
    gray = _img(out).convert("L")
    assert regions, "no regions came back"

    # Ink is measured relative to the paper, not against an absolute value.
    # At the heavy level a single-character cell is 4x5 pixels and contrast
    # loss lifts its darkest pixel to about 195: genuinely faint, and still
    # unambiguously darker than its surroundings. An absolute threshold
    # confused "washed out" with "in the wrong place", which are the two
    # things this test has to keep apart.
    paper = gray.getextrema()[1]
    for name, (x0, y0, x1, y1) in regions.items():
        darkest = gray.crop((x0, y0, x1, y1)).getextrema()[0]
        assert darkest < paper - 20, (
            f"{level}: region for {name} at {(x0, y0, x1, y1)} is no darker "
            f"than the paper ({darkest} vs {paper}); regions did not follow "
            f"the image")


@pytest.mark.parametrize("level", LEVEL_NAMES)
def test_regions_stay_inside_the_image(level):
    doc, png = _rendered(2)
    out, regions = degrade(png, level, seed=3, regions=doc.regions)
    im = _img(out)
    for name, (x0, y0, x1, y1) in regions.items():
        assert 0 <= x0 < x1 <= im.width, f"{level}/{name} x: {(x0, x1)}"
        assert 0 <= y0 < y1 <= im.height, f"{level}/{name} y: {(y0, y1)}"


def test_a_deliberately_wrong_transform_fails_the_ink_test():
    """Mutation check on the test above.

    If regions that were NOT transformed still passed, the ink test would be
    proving nothing. Feed the heavy level the original coordinates and require
    that it fails.
    """
    doc, png = _rendered(0)
    out, moved = degrade(png, HEAVY, seed=1, regions=doc.regions)
    gray = _img(out).convert("L")
    w, h = gray.size

    def usable(boxes) -> float:
        """Fraction of boxes that are on the canvas AND sitting on ink.

        Falling off the canvas counts as a failure instead of being skipped.
        Skipping them discards most of the sample at the heavy level and
        leaves the check passing on four boxes that happen to land in the
        dense top-left corner.
        """
        paper = gray.getextrema()[1]
        ok = 0
        for x0, y0, x1, y1 in boxes:
            if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
                continue
            if gray.crop((x0, y0, x1, y1)).getextrema()[0] < paper - 20:
                ok += 1
        return ok / len(boxes)

    transformed = usable(moved.values())
    untransformed = usable(doc.regions.values())

    assert transformed == 1.0, (
        f"transformed regions are not all valid: {transformed}")
    assert untransformed < 0.5, (
        f"{untransformed:.0%} of untransformed regions still look valid, so "
        f"the ink test cannot distinguish a correct transform from none")


def test_clean_level_is_a_true_no_op():
    """The control arm must be the render itself, byte for byte. If 'clean'
    silently re-encoded the image, every level would be degraded and the
    baseline would not be a baseline."""
    doc, png = _rendered(4)
    out, regions = degrade(png, CLEAN, seed=0, regions=doc.regions)
    assert regions == {k: tuple(v) for k, v in doc.regions.items()}
    assert _img(out).tobytes() == _img(png).tobytes()


def test_levels_are_monotonically_worse():
    """Each level must be measurably harder than the one before it.

    Measured as mean absolute difference from the clean page, resampled to a
    common size. If two levels do not differ, reporting them as separate
    conditions would be inventing a distinction.
    """
    from PIL import Image

    doc, png = _rendered(5)
    base = _img(png).convert("L")
    scores = []
    for level in LEVEL_NAMES:
        out, _ = degrade(png, level, seed=7, regions=doc.regions)
        im = _img(out).convert("L").resize(base.size, Image.LANCZOS)
        diff = sum(abs(a - b) for a, b in
                   zip(base.get_flattened_data(), im.get_flattened_data()))
        scores.append(diff / (base.width * base.height))
    assert scores[0] == 0, "clean differs from the original"
    assert scores[1] < scores[2] < scores[3], (
        f"levels are not monotonic: {[round(s, 2) for s in scores]}")


def test_heavy_falls_below_the_legibility_floor():
    """The heavy level is meant to be genuinely hard, not only annotated as
    hard. 150 DPI text at 0.52 is 78 DPI, below the ~110 DPI floor named in
    render.py."""
    assert effective_dpi(HEAVY) < 110
    assert effective_dpi(MODERATE) > effective_dpi(HEAVY)
    assert effective_dpi(CLEAN) == 150


def test_degradation_is_deterministic():
    """Same document, same level, same seed, same bytes. Otherwise a
    difference between two runs cannot be attributed to the model."""
    doc, png = _rendered(6)
    a, ra = degrade(png, MODERATE, seed=11, regions=doc.regions)
    b, rb = degrade(png, MODERATE, seed=11, regions=doc.regions)
    assert a == b and ra == rb


def test_different_seeds_give_different_skew():
    """Skew must actually vary, or every page in a level is the same page."""
    doc, png = _rendered(6)
    a, _ = degrade(png, HEAVY, seed=1, regions=doc.regions)
    b, _ = degrade(png, HEAVY, seed=2, regions=doc.regions)
    assert a != b


@pytest.mark.parametrize("level", LEVEL_NAMES)
def test_degraded_pages_do_not_get_more_expensive(level):
    """Degradation must not silently raise the bill. Downsampled levels should
    cost fewer visual tokens; skew expands the canvas slightly, so the light
    level is allowed a small increase but not a large one."""
    from vei.render import visual_tokens_for

    doc, png = _rendered(0)
    clean_tokens, _, _ = visual_tokens_for(png)
    out, _ = degrade(png, level, seed=1, regions=doc.regions)
    tokens, _, _ = visual_tokens_for(out)
    assert tokens <= clean_tokens * 1.25, (
        f"{level} costs {tokens} tokens vs {clean_tokens} clean")
    if LEVELS[level].scale < 1.0:
        assert tokens < clean_tokens


def test_rotation_alone_moves_the_regions_with_the_ink():
    """The rotation half of the transform, isolated.

    The heavy-level check above also rescales, and a missing rescale is
    enough to fail it, so it cannot show that the rotation step moves the
    regions. A pure rotation, no rescale and no noise, separates the two: the
    moved boxes must all sit on ink, and the unmoved ones must mostly not.
    """
    import random

    from vei.degrade import Level, _apply

    doc, png = _rendered(0)
    spec = Level("rotate_only", 1.0, 6.0, 0.0, 0.0, 100, 1.0, "rotation only")
    out, moved = _apply(png, spec, random.Random("rotate-only"),
                        dict(doc.regions))
    gray = _img(out).convert("L")
    paper = gray.getextrema()[1]
    w, h = gray.size

    def on_ink(boxes) -> float:
        hits = 0
        for x0, y0, x1, y1 in boxes:
            if (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h
                    and gray.crop((x0, y0, x1, y1)).getextrema()[0] < paper - 20):
                hits += 1
        return hits / len(boxes)

    assert on_ink(moved.values()) == 1.0
    # Measured 0.78 at this angle: text lines are long, so an unrotated box
    # often still lands on some ink. What matters is that it is not all of
    # them, which is what the first assertion requires of the moved boxes.
    assert on_ink(doc.regions.values()) < 0.9, (
        "unrotated boxes all land on ink, so this check cannot see a "
        "rotation that left the regions behind")
