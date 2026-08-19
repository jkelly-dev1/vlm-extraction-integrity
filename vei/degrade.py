"""Scan degradation: turn a crisp render into something like a real document.

Why this exists. The clean-corpus probe returned 38 correct extractions out of
38. That is a real result and it is also the easy case: production document
intelligence does not run on crisp digital renders, it runs on phone
photographs, faxes, and generational photocopies. This module produces the
hard case, so the premise can be tested rather than assumed.

FOUR LEVELS, and the degradations are the ones that actually happen:

  clean     the render, untouched. The control arm.
  light     a good office scan: slight skew, mild JPEG, a little sensor noise.
  moderate  a phone photo of a printed page: visible skew, downsampled,
            softened, moderate JPEG.
  heavy     a fax or a third-generation photocopy: heavy downsample below the
            legibility floor, real skew, blur, coarse JPEG, washed contrast.

The ground truth moves with the pixels, and that is the dangerous part.
Rescaling and rotation relocate every glyph. If the recorded regions were left
alone, every grounding measurement on a degraded page would be graded against
coordinates from a different image, and it would still produce a confident
table full of plausible numbers. So degrade() transforms the regions with the
same affine it applies to the image, and tests/test_degrade.py checks the
result the only way that really settles it: by looking at whether there is
still ink inside each region.

Rotation makes an axis-aligned box into a rotated quad. The transformed region
is the bounding box of that quad, which is slightly LOOSER than the true glyph
bounds. Loose in this direction is the safe direction, it can let a sloppy
citation pass, never reject an accurate one. It is a real limit on what a
grounding number from a rotated page means.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass

CLEAN = "clean"
LIGHT = "light"
MODERATE = "moderate"
HEAVY = "heavy"

LEVEL_NAMES = (CLEAN, LIGHT, MODERATE, HEAVY)


@dataclass(frozen=True)
class Level:
    """One degradation recipe. Every number here is a knob a real scanner has."""

    name: str
    scale: float          # resample factor; 0.55 of 150 DPI is about 82 DPI
    max_rotate: float     # degrees, sampled in [-max, +max]
    blur: float           # gaussian radius in pixels
    noise: float          # target noise standard deviation, 0-255 scale
    jpeg_quality: int     # 100 means no JPEG round trip
    contrast: float       # 1.0 leaves it alone; below 1.0 washes it out
    description: str


# Noise is deliberately low, and the obvious settings are far too high.
#
# Noise sd 6/12/20 at these blend ratios buries the page in static. The heavy
# level is then not hard to read, it is impossible to read. That measures "an
# unreadable page produces a failure", which is both obvious and structural,
# and says nothing about the subtle wrong-value errors this repository exists
# to measure.
#
# The degradation that produces a realistic MISREAD is loss of resolution plus
# blur plus JPEG blocking: that is what makes a 5 look like a 6 and an 8 look
# like a 0 while the page still reads as a purchase order. Noise is a garnish
# on top of it, not the mechanism. Checked by eye at every level, because no
# assertion available here distinguishes "hard" from "hopeless".
LEVELS = {
    CLEAN: Level(CLEAN, 1.0, 0.0, 0.0, 0.0, 100, 1.0,
                 "untouched render (control arm)"),
    LIGHT: Level(LIGHT, 1.0, 0.4, 0.3, 2.0, 85, 0.98,
                 "good office scanner"),
    MODERATE: Level(MODERATE, 0.72, 1.2, 0.6, 4.0, 60, 0.92,
                    "phone photo of a printed page"),
    HEAVY: Level(HEAVY, 0.52, 2.5, 0.8, 7.0, 40, 0.82,
                 "fax or third-generation photocopy"),
}


def _rotate_point(x: float, y: float, angle_deg: float,
                  cx: float, cy: float, ncx: float, ncy: float
                  ) -> tuple[float, float]:
    """Map a point through PIL's rotate(angle, expand=True).

    PIL rotates counter-clockwise. In image coordinates (y increasing
    downward) that is the transform below. The sign is easy to get backwards
    and a wrong sign still produces boxes that look reasonable, so
    test_regions_still_contain_ink checks pixels rather than arithmetic.
    """
    a = math.radians(angle_deg)
    dx, dy = x - cx, y - cy
    return (math.cos(a) * dx + math.sin(a) * dy + ncx,
            -math.sin(a) * dx + math.cos(a) * dy + ncy)


def degrade(png: bytes, level: str, *, seed: int = 0,
            regions: dict[str, tuple[int, int, int, int]] | None = None
            ) -> tuple[bytes, dict[str, tuple[int, int, int, int]]]:
    """Degrade `png` to `level`. Returns (png_bytes, transformed_regions).

    Deterministic in `seed`: the same document at the same level is always
    degraded identically, so a difference between two runs is a difference in
    the model and not in the paper.
    """
    return _apply(png, LEVELS[level],
                  random.Random(f"vei-degrade-{level}-{seed}"),
                  dict(regions or {}))


def _apply(png: bytes, spec: "Level", rng: random.Random,
           regions: dict) -> tuple[bytes, dict]:
    """The whole degradation pipeline. One implementation, two entry points."""
    from PIL import Image, ImageEnhance, ImageFilter

    img = Image.open(io.BytesIO(png)).convert("RGB")

    # -- 1. resample -------------------------------------------------------
    if spec.scale != 1.0:
        w, h = img.size
        nw, nh = max(1, round(w * spec.scale)), max(1, round(h * spec.scale))
        # The real scale is nw/w, not spec.scale, because of the rounding.
        # Using the requested factor here would drift the regions off the
        # glyphs by a fraction of a pixel per call, which is invisible until
        # it is not.
        sx, sy = nw / w, nh / h
        img = img.resize((nw, nh), Image.LANCZOS)
        regions = {k: (v[0] * sx, v[1] * sy, v[2] * sx, v[3] * sy)
                   for k, v in regions.items()}

    # -- 2. skew -----------------------------------------------------------
    if spec.max_rotate:
        angle = rng.uniform(-spec.max_rotate, spec.max_rotate)
        w, h = img.size
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True,
                         fillcolor=(255, 255, 255))
        nw, nh = img.size
        cx, cy, ncx, ncy = w / 2, h / 2, nw / 2, nh / 2
        moved = {}
        for k, (x0, y0, x1, y1) in regions.items():
            pts = [_rotate_point(x, y, angle, cx, cy, ncx, ncy)
                   for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            moved[k] = (min(xs), min(ys), max(xs), max(ys))
        regions = moved

    # -- 3. optics and sensor ---------------------------------------------
    if spec.blur:
        img = img.filter(ImageFilter.GaussianBlur(spec.blur))
    if spec.noise:
        # Deterministic noise, generated from `rng`, NOT Image.effect_noise().
        #
        # effect_noise() draws from PIL's C-level generator, which cannot be
        # seeded from Python: the same document at the same level produced
        # different bytes on every call. That silently breaks the premise of
        # the whole comparison. A difference between two runs is supposed to
        # be a difference in the MODEL, not in the paper. Caught by
        # test_degradation_is_deterministic.
        #
        # This is uniform noise blended to a target standard deviation, not
        # gaussian. Uniform over [0, 255] has sd 255/sqrt(12) = 73.6, so
        # blending at alpha yields sd = 73.6 * alpha. Named accurately here
        # because "gaussian sigma" would have been a claim nothing enforces.
        w, h = img.size
        flat = Image.frombytes("L", (w, h), rng.randbytes(w * h))
        alpha = min(1.0, spec.noise / 73.6)
        img = Image.blend(img, flat.convert("RGB"), alpha)
    if spec.contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(spec.contrast)

    # -- 4. lossy transport ------------------------------------------------
    if spec.jpeg_quality < 100:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=spec.jpeg_quality)
        buf.seek(0)
        img = Image.open(buf).convert("RGB")

    # Clamp to the final image and round. Clamping matters because rotation
    # can push a box a fraction of a pixel outside the canvas.
    w, h = img.size
    out_regions = {}
    for k, (x0, y0, x1, y1) in regions.items():
        x0 = max(0, min(w - 1, int(math.floor(x0))))
        y0 = max(0, min(h - 1, int(math.floor(y0))))
        x1 = max(x0 + 1, min(w, int(math.ceil(x1))))
        y1 = max(y0 + 1, min(h, int(math.ceil(y1))))
        out_regions[k] = (x0, y0, x1, y1)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), out_regions


def resolution_only(png: bytes, target_dpi: int, *, base_dpi: int = 150,
                    regions: dict | None = None) -> tuple[bytes, dict]:
    """Rescale to `target_dpi` and change NOTHING else.

    The four named LEVELS move scale, skew, blur, noise and JPEG together.
    Realistic, and useless for locating a threshold, because if the error rate
    moves you cannot say which knob moved it. A DPI sweep needs exactly one
    variable, so this holds every other degradation at zero.

    Registering a temporary entry in the LEVELS dict would also work, but it
    mutates module state from a caller, which is fine until two callers do it
    at once. A function is the same thing without the footgun.
    """
    scale = target_dpi / base_dpi
    spec = Level(f"res_{target_dpi}", scale, 0.0, 0.0, 0.0, 100, 1.0,
                 f"resolution only, {target_dpi} DPI")
    return _apply(png, spec, random.Random(f"vei-res-{target_dpi}"),
                  dict(regions or {}))


def effective_dpi(level: str, base_dpi: int = 150) -> float:
    """The DPI a page at `level` actually presents to the model.

    Reported alongside results because it is the number that predicts
    legibility, and 'scale 0.55' does not read as 'below the floor'.
    """
    return base_dpi * LEVELS[level].scale
