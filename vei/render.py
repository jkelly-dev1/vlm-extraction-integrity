"""Render a Document to PNG and record the pixel region of every value.

The regions are the reason this project can grade grounding at all: they are
recorded at draw time from the same text-measurement call that positions the
glyphs, so they are exact by construction rather than annotated afterwards.

DEFAULT 150 DPI, and that is not arbitrary. On the 28x28-pixel-patch
accounting used by the Claude API, a US Letter page costs:

    110 DPI    935 x 1210    1,496 visual tokens
    150 DPI   1275 x 1650    2,714 visual tokens
    200 DPI   1700 x 2200    4,819  -- over the ~4,784 per-image cap
    300 DPI   2550 x 3300   10,856  -- far over it

Anything above roughly 150 DPI is downscaled before the model sees it, so
rendering higher costs preparation time and buys no fidelity. 150 sits just
above the ~110 DPI legibility floor for 8-point text with headroom for a
degraded-scan variant.
"""

from __future__ import annotations

import importlib.util
import io

from . import fonts
from .docs import Document, cents
from .fonts import font_path

LETTER_IN = (8.5, 11.0)
DEFAULT_DPI = 150

# Which drawing this is. Recorded into every paid artifact, because a
# compliance rate is a measurement OF A STIMULUS and two runs drawn differently
# are not comparable no matter how identical the rest of the harness is.
#
# 2026-08-11-flat  every value drawn on one line. The metadata_field injection
#                  replaces `terms` with a 160-character sentence 754px wide,
#                  and `currency` is drawn afterwards at x=637 on the same
#                  baseline, so USD was painted ON TOP of the injected
#                  subtotal digits, and the currency cell was destroyed.
# 2026-08-12-terms-wrap  `terms` wraps before it reaches the second column.
#                  Clean pages are byte-identical to the previous revision;
#                  only a long terms value is drawn differently.
RENDER_REVISION = "2026-08-12-terms-wrap"


def render_blocked_by() -> str:
    """What stops this machine drawing a page, in words. Empty when nothing does.

    fonts.available() answers half the question and reads like the whole one.
    The offline half of this repository is meant to run with pytest and nothing
    else installed, so a caller asking only about fonts gets True on a machine
    with no Pillow and raises ImportError three lines later. Both halves are
    asked here, once, and the answer says which one is missing rather than
    leaving the reader to guess from an ImportError.

    Pillow is checked by spec rather than by importing it: this module is
    imported by the offline half, and importing Pillow to find out whether
    Pillow is installed would defeat the point.
    """
    missing = []
    if importlib.util.find_spec("PIL") is None:
        missing.append("Pillow is not installed")
    if not fonts.available():
        missing.append("no TrueType font was found")
    return " and ".join(missing)


def can_render() -> bool:
    """True when a page can actually be drawn: Pillow present AND a font found."""
    return not render_blocked_by()


def _px(dpi: int) -> tuple[int, int]:
    return int(LETTER_IN[0] * dpi), int(LETTER_IN[1] * dpi)


def _wrap(text: str, measure, max_w: int) -> list[str]:
    """Break `text` on word boundaries into lines no wider than `max_w`.

    A single word wider than `max_w` is left on its own line and overflows.
    That is deliberate: silently truncating it would mean the page no longer
    says what the ground truth says it says, which is worse than a wide line.
    No word in this corpus or in any injection is anywhere near that wide.
    """
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if cur and measure(trial) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines or [""]


def render(doc: Document, *, dpi: int = DEFAULT_DPI, family: str = "sans",
           record: bool = True, crop: bool = True) -> bytes:
    """Draw `doc` and return PNG bytes. Fills `doc.regions` in place.

    `doc.regions` maps a field name to (x0, y0, x1, y1) in image pixels. Line
    item cells are keyed "line_items[i].<field>", 0-indexed to match the
    payload rather than the printed row number.

    `crop` trims the blank tail of the sheet; see the note where it happens.
    """
    # Imported locally so the offline half never needs Pillow installed.
    from PIL import Image, ImageDraw, ImageFont

    W, H = _px(dpi)
    scale = dpi / 150.0
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def f(size: int, bold: bool = False):
        return ImageFont.truetype(font_path(family, bold), int(size * scale))

    body, label, head, title = f(9), f(8), f(9, True), f(18, True)
    margin = int(72 * scale)
    y = margin

    def put(text: str, x: int, yy: int, font, key: str | None = None) -> int:
        d.text((x, yy), text, fill="black", font=font)
        if record and key is not None:
            doc.regions[key] = tuple(int(v) for v in
                                     d.textbbox((x, yy), text, font=font))
        return yy + int(font.size * 1.6)

    def put_wrapped(text: str, x: int, yy: int, font, key: str, max_w: int):
        """Draw `text` over as many lines as it needs, inside `max_w`.

        Records ONE region: the union of the lines. A citation is graded by
        whether it lands on the region, and a value printed across two lines
        occupies both of them, so the union is the correct region.
        Single-line text takes exactly the same code path as put() and
        produces identical pixels and an identical region.
        """
        box = None
        for line in _wrap(text, lambda s: d.textlength(s, font=font), max_w):
            bb = d.textbbox((x, yy), line, font=font)
            d.text((x, yy), line, fill="black", font=font)
            box = bb if box is None else (min(box[0], bb[0]),
                                          min(box[1], bb[1]),
                                          max(box[2], bb[2]),
                                          max(box[3], bb[3]))
            yy += int(font.size * 1.6)
        if record and box is not None:
            doc.regions[key] = tuple(int(v) for v in box)
        return yy

    # -- header ------------------------------------------------------------
    put("PURCHASE ORDER", margin, y, title)
    y += int(34 * scale)

    col2 = W // 2
    put("PO Number", margin, y, label)
    put("Order Date", col2, y, label)
    y2 = y + int(14 * scale)
    put(doc.po_number, margin, y2, body, "po_number")
    y = put(doc.order_date, col2, y2, body, "order_date")

    y += int(6 * scale)
    put("Vendor", margin, y, label)
    put("Due Date", col2, y, label)
    y2 = y + int(14 * scale)
    put(doc.vendor, margin, y2, body, "vendor")
    y = put(doc.due_date, col2, y2, body, "due_date")

    y += int(6 * scale)
    put("Terms", margin, y, label)
    put("Currency", col2, y, label)
    y2 = y + int(14 * scale)
    # Terms is the only field on this page whose length is not bounded by the
    # generator, because the metadata_field injection replaces it with a whole
    # sentence. Until 2026-08-12 it was drawn on one line at x=margin and
    # `currency` was then drawn at x=col2 on the SAME baseline, so on every
    # injected page USD was painted over the middle of the injected sentence,
    # on the subtotal digits, which is precisely the value the injection needs
    # the model to copy, and the currency cell was destroyed. Both effects were
    # invisible in the artifacts, which recorded the verdict and not the page.
    # Test_no_two_regions_overlap now fails if this comes back.
    y = max(put_wrapped(doc.terms, margin, y2, body, "terms",
                        col2 - margin - int(12 * scale)),
            put(doc.currency, col2, y2, body, "currency"))

    # -- line item table ---------------------------------------------------
    y += int(22 * scale)
    cols = [margin,
            margin + int(40 * scale),
            margin + int(130 * scale),
            margin + int(390 * scale),
            margin + int(460 * scale),
            margin + int(560 * scale)]
    for text, x in zip(("#", "SKU", "Description", "Qty", "Unit", "Amount"), cols):
        d.text((x, y), text, fill="black", font=head)
    y += int(16 * scale)
    d.line([(margin, y), (W - margin, y)], fill="black", width=max(1, int(scale)))
    y += int(8 * scale)

    for i, item in enumerate(doc.items):
        put(str(item.row), cols[0], y, body, f"line_items[{i}].row")
        put(item.sku, cols[1], y, body, f"line_items[{i}].sku")
        put(item.description, cols[2], y, body, f"line_items[{i}].description")
        put(str(item.quantity), cols[3], y, body, f"line_items[{i}].quantity")
        put(cents(item.unit_price_cents), cols[4], y, body,
            f"line_items[{i}].unit_price_cents")
        y = put(cents(item.amount_cents), cols[5], y, body,
                f"line_items[{i}].amount_cents")

    # -- totals ------------------------------------------------------------
    y += int(10 * scale)
    d.line([(cols[4], y), (W - margin, y)], fill="black",
           width=max(1, int(scale)))
    y += int(10 * scale)
    for lbl, val, key in (("Subtotal", doc.subtotal_cents, "subtotal_cents"),
                          ("Tax 8.25%", doc.tax_cents, "tax_cents"),
                          ("TOTAL", doc.total_cents, "total_cents")):
        d.text((cols[4], y), lbl, fill="black",
               font=head if key == "total_cents" else label)
        y = put(cents(val), cols[5], y,
                head if key == "total_cents" else body, key)

    # -- crop the empty tail of the page ------------------------------------
    # A Letter page of this document is about three quarters blank, and blank
    # paper is billed. Measured on document PO00000 at 150 DPI: the full sheet
    # is 1275x1650 for 2,714 visual tokens, the cropped page 1228x414 for 660.
    # A 4.1x cost reduction on every call for no loss of information.
    # test_crop_is_a_real_saving asserts the saving is at least 2x, so this
    # comment cannot drift into being a claim nothing checks.
    #
    # Only the bottom and right are removed, so the origin does not move and
    # every region already recorded stays valid. Cropping the top would
    # silently invalidate the entire grounding ground truth, and that must
    # not be quietly wrong.
    if crop:
        ink = img.convert("L").point(lambda v: 0 if v > 250 else 255)
        box = ink.getbbox()
        if box:
            pad = int(24 * scale)
            img = img.crop((0, 0, min(W, box[2] + pad), min(H, box[3] + pad)))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def visual_tokens(dpi: int = DEFAULT_DPI, patch: int = 28) -> int:
    """Visual tokens for a FULL uncropped page at `dpi`.

    `patch=28` is the Claude API's patch size. Kept for the DPI table in this
    module's docstring; it is an upper bound, not what a call actually costs,
    because render() crops. Cost estimates must use visual_tokens_for() on a
    real rendered page instead. An estimate built on this function would
    overstate the bill by roughly 3x and, worse, would keep looking right.
    """
    W, H = _px(dpi)
    return -(-W // patch) * -(-H // patch)


def visual_tokens_for(png: bytes, patch: int = 28) -> tuple[int, int, int]:
    """(tokens, width, height) for an actual rendered image."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        w, h = im.size
    return -(-w // patch) * -(-h // patch), w, h
