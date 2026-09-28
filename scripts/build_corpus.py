#!/usr/bin/env python3
"""Render the corpus to PNGs plus a ground-truth JSON. Offline, free.

    python scripts/build_corpus.py --n 20 --out audit/corpus

Writes doc_00000.png ... and ground_truth.json holding, per document, the
truth payload and every field's pixel region. The regions are what makes a
grounding claim checkable rather than assertable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vei.docs import corpus                                    # noqa: E402
from vei.render import DEFAULT_DPI, render, visual_tokens_for  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", type=Path, default=Path("audit/corpus"))
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--control", action="store_true",
                    help="build the non-confusable control corpus instead")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    truth = {}
    total_bytes = 0
    # Measured on the pages actually written, which are cropped to their ink.
    # visual_tokens() is the full uncropped sheet and overstates it about 4x.
    tokens = []
    for doc in corpus(args.n, confusable=not args.control):
        png = render(doc, dpi=args.dpi)
        (args.out / f"{doc.doc_id}.png").write_bytes(png)
        total_bytes += len(png)
        tokens.append(visual_tokens_for(png)[0])
        truth[doc.doc_id] = {
            "truth": doc.truth(),
            "regions": {k: list(v) for k, v in doc.regions.items()},
        }

    meta = {
        "n": args.n,
        "dpi": args.dpi,
        "confusable": not args.control,
        "visual_tokens_per_page_max": max(tokens),
        "documents": truth,
    }
    (args.out / "ground_truth.json").write_text(json.dumps(meta, indent=2) + "\n")

    print(f"wrote {args.n} pages to {args.out}  "
          f"({total_bytes / 1024:.0f} KB, at most {max(tokens):,} "
          f"visual tokens/page at {args.dpi} DPI)")
    print(f"ground truth: {args.out / 'ground_truth.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
