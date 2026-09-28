#!/usr/bin/env python3
"""Regenerate Images/: the pages a reader can actually look at. Free.

    python scripts/build_images.py

Images/ is tracked in the repository. Every claim in the README is about what a
model could or could not read off a page, and a reader has no way to judge "108
DPI was legible, 78 DPI was marginal" from a number. The pictures are the
evidence.

Deterministic: same corpus index, same seeds, same bytes every run.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vei.degrade import (LEVEL_NAMES, LEVELS, degrade,           # noqa: E402
                         effective_dpi, resolution_only)
from vei.docs import cents, make_document                       # noqa: E402
from vei.inject import (CHANNELS, CONSISTENT, METADATA,        # noqa: E402
                        VISIBILITY, render_with_injection)
from vei.render import render, visual_tokens_for                # noqa: E402

DOC_INDEX = 0
SWEEP_DPI = (150, 130, 120, 110, 100, 90, 80)


def _save(path: Path, png: bytes) -> str:
    path.write_bytes(png)
    tokens, w, h = visual_tokens_for(png)
    return f"{w}x{h} px, {tokens:,} visual tokens, {len(png) / 1024:.0f} KB"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("Images"))
    args = ap.parse_args()
    out = args.out
    (out / "degradation").mkdir(parents=True, exist_ok=True)
    (out / "injection").mkdir(parents=True, exist_ok=True)
    (out / "dpi_sweep").mkdir(parents=True, exist_ok=True)

    lines = []
    doc = make_document(DOC_INDEX)
    clean_png = render(doc)
    info = _save(out / "01_clean_document.png", clean_png)
    lines.append("| `01_clean_document.png` | the corpus page as rendered "
                 f"| {info} |")

    # A close crop of the confusable rows, because the trap is invisible at
    # full-page scale and the reason the corpus exists.
    from PIL import Image
    im = Image.open(io.BytesIO(clean_png))
    boxes = [doc.regions[f"line_items[{i}].amount_cents"] for i in (1, 2)]
    x0 = min(b[0] for b in boxes) - 420
    y0 = min(b[1] for b in boxes) - 22
    x1 = max(b[2] for b in boxes) + 40
    y1 = max(b[3] for b in boxes) + 22
    crop = im.crop((max(0, x0), max(0, y0), x1, y1))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    info = _save(out / "02_confusable_rows.png", buf.getvalue())
    a, b = (cents(doc.items[i].amount_cents) for i in (1, 2))
    lines.append(f"| `02_confusable_rows.png` | rows 2 and 3 at 3x: {a} vs {b} "
                 f"-- the trap | {info} |")

    deg_lines = []
    for lv in LEVEL_NAMES:
        d = make_document(DOC_INDEX)
        base = render(d)
        png, _ = degrade(base, lv, seed=1, regions=d.regions)
        info = _save(out / "degradation" / f"{lv}.png", png)
        deg_lines.append(f"| `{lv}.png` | {LEVELS[lv].description} | "
                         f"{effective_dpi(lv):.0f} DPI | {info} |")

    inj_lines = []
    for ch in CHANNELS:
        d = make_document(DOC_INDEX)
        png, inj = render_with_injection(d, ch)
        info = _save(out / "injection" / f"{ch}.png", png)
        inj_lines.append(f"| `{ch}.png` | {VISIBILITY[ch]} | "
                         f"`{inj.text}` | {info} |")

    # The page the metadata_field compliance numbers were measured on. All six
    # images above carry the LOUD ask, which is short. The CONSISTENT ask is a
    # 160-character sentence, long enough to run through the Currency column
    # if it is drawn in the wrong place, and a reader can see where it lands
    # only on a published page.
    d = make_document(DOC_INDEX)
    png, inj = render_with_injection(d, METADATA, kind=CONSISTENT)
    info = _save(out / "injection" / "metadata_field_consistent.png", png)
    inj_lines.append(f"| `metadata_field_consistent.png` | "
                     f"{VISIBILITY[METADATA]} | `{inj.text}` | {info} |")

    sweep_lines = []
    for dpi in SWEEP_DPI:
        d = make_document(DOC_INDEX)
        base = render(d)
        png, _ = resolution_only(base, dpi, regions=d.regions)
        info = _save(out / "dpi_sweep" / f"{dpi:03d}dpi.png", png)
        sweep_lines.append(f"| `{dpi:03d}dpi.png` | {dpi} DPI | {info} |")

    (out / "README.md").write_text(_readme(lines, deg_lines, inj_lines,
                                           sweep_lines))
    print(f"wrote {out}/ -- "
          f"{3 + len(LEVEL_NAMES) + len(CHANNELS) + len(SWEEP_DPI)} images")
    return 0


def _readme(main_rows, deg_rows, inj_rows, sweep_rows) -> str:
    return f"""# Images

Every page the measurements were taken on, so a reader can judge the claims
about legibility instead of taking them on trust. Regenerate with:

    python scripts/build_images.py

All of these are document index 0 of the synthetic corpus, rendered
deterministically. Nothing here is a real document from anywhere.

## The page and the trap

| file | what it is | size |
|---|---|---|
{chr(10).join(main_rows)}

`02_confusable_rows.png` is the one to look at first. Rows 2 and 3 carry
amounts within 40 cents of each other by construction, so a model that reads
the wrong row produces a value that is well formed, in range, and wrong.

## Degradation levels

Used by `scripts/degrade_run.py`. These vary scale, skew, blur, noise and JPEG
quality together, to imitate real scanning paths.

| file | imitates | effective | size |
|---|---|---|---|
{chr(10).join(deg_rows)}

Look at `moderate.png` and `heavy.png` side by side. `heavy` is obviously
damaged and produced a 100% error rate that cross-field arithmetic caught
every time. `moderate` looks basically fine and is the dangerous one: 76% error
rate, and almost none of it visible to any check that reads only the payload.

## Injection channels

Instructions printed on the document itself, ordered roughly by how visible they
are to a human reviewer. The first six ask for the same thing so the channels
are comparable: the LOUD goal, a total of $1.00.

The last row is the odd one, and the important one:
`metadata_field_consistent.png` carries the CONSISTENT goal, a self-consistent
under-billing, and the `metadata_field` compliance numbers were measured on
this page. It is a 160-character sentence rather than a short note, so it
wraps. Before 2026-08-12 it was drawn straight through the Currency column with
`USD` printed across its subtotal digits.

The other five channels carried the same sentence in the one consistent run
that measured them (n=15) and are not pictured. On `plain_note`,
`authority_framing` and `low_contrast` it is drawn across the TOTAL row, and
the `rotated_margin` strip cuts it off before its numbers; INJECTION.md says
what that means for their 0/15.

| file | visibility to a reviewer | printed text | size |
|---|---|---|---|
{chr(10).join(inj_rows)}

`low_contrast.png` is worth opening at full size. The instruction is light gray
on white. Easy for an eye to skim past, and ordinary ink to a model, which sees
pixel values and has no notion of "faint".

## DPI sweep

Resolution varied ALONE, with skew, blur, noise and JPEG all held at zero, so
that any change in accuracy is attributable to resolution and nothing else.

| file | resolution | size |
|---|---|---|
{chr(10).join(sweep_rows)}
"""


if __name__ == "__main__":
    raise SystemExit(main())
