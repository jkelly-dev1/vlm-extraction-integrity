#!/usr/bin/env python3
"""Run the full defense ladder against the stub adapter. No network, no cost.

    python scripts/offline_demo.py
    python scripts/offline_demo.py --docs 200 --json audit/offline.json

What this measures: the LADDER, not a model. Every payload comes from
vei.errors, which constructs each error class by hand. That makes the catch
rates exact rather than sampled, but it means these numbers say nothing
about how often a real VLM commits each error. The real run measures that,
separately, and the two must never be reported as one figure.

RUNG 5's catch rate is not one number. Whether grounding can see a wrong
value depends entirely on an assumption that has to be stated: where a model
that misread a field says it looked. Both are reported, because the gap
between them is larger than most differences this repository reports as
findings.

  Cites where it looked   the model points at the box it actually read, which
                          is not the one it claims. Rung 5 fires. This is the
                          primary measurement as of 2026-08-12 and it is what
                          the tables below report.
  Cites the True region   a confident misread: the model believes it read the
                          right box and points at it. The citation check can
                          never fire, so rung 5 is left with only its row-count
                          check. This is what every offline table published
                          before 2026-08-12 assumed, silently, and it is
                          reported alongside as the conservative bound.

Why the primary changed. Under the conservative assumption rung 5's citation
comparison contributes NOTHING to any number in these tables, its whole column
comes from the row-count check, so a reader who takes 20% as evidence about
grounding is reading a figure that grounding did not produce.

Near-miss and far-miss are indistinguishable HERE, because the shipped rung
asks only whether the boxes touch. They stop being equivalent the moment any
tolerance is added, which is what scripts/tolerance_sweep.py is for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vei.docs import corpus                                    # noqa: E402
from vei.errors import ERROR_CLASSES                           # noqa: E402
from vei.grade import Results, table, wilson                   # noqa: E402
from vei.ladder import LADDER, RUNG_NAMES                      # noqa: E402
from vei.models import StubModel                               # noqa: E402
from vei.render import render_blocked_by                       # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=int, default=120,
                    help="documents per error class (default 120)")
    ap.add_argument("--json", type=Path, default=None,
                    help="write the full result object here")
    ap.add_argument("--no-render", action="store_true",
                    help="skip rendering; grounding rungs then have no regions "
                         "to check against and will be reported as such")
    args = ap.parse_args()

    # Both halves, not just the font. Fonts.available() is True on a machine
    # with fonts and no Pillow, and render() below would then raise ImportError
    # on its first call, which is how this script broke in CI.
    blocked = render_blocked_by()
    render_ok = not args.no_render and not blocked
    if not args.no_render and blocked:
        print(f"NOTE: {blocked}, so this runs without rendered regions.\n"
              "      Rungs 5 and 6 cannot check citations without them.\n",
              file=sys.stderr)

    docs = list(corpus(args.docs))

    if render_ok:
        from vei.render import render
        for d in docs:
            render(d)   # fills d.regions in place

    def measure(honest: bool) -> Results:
        """One clean pass, then one pass per error class."""
        stub = StubModel()
        out = Results()
        for d in docs:
            ex = stub.extract(d, error_class=None, honest_citations=honest)
            for name, fn in LADDER:
                out.record(name, None,
                           not fn(ex.payload, d, ex.evidence).accepted)
        for cls in ERROR_CLASSES:
            for d in docs:
                ex = stub.extract(d, error_class=cls,
                                  honest_citations=honest)
                if ex.error_class is None:
                    continue  # corruption not applicable to this document
                for name, fn in LADDER:
                    v = fn(ex.payload, d, ex.evidence)
                    out.record(name, ex.error_class, not v.accepted)
        return out

    stub = StubModel()
    # Primary and secondary, named for what they assume rather than for which
    # one happens to be printed first.
    results = measure(honest=False)      # cites where it looked
    conservative = measure(honest=True)  # cites the true region

    print(f"vlm-extraction-integrity -- offline ladder measurement")
    print(f"adapter: stub (no model was called)   documents/class: {args.docs}")
    # With no regions the stub cites nothing, so rung 5 rejects every payload
    # for "no citation" and rungs 5 to 7 print 100% for every class. That is
    # a rung with nothing to check against, not a rung that works.
    regions_note = ("yes" if render_ok else
                    "NO -- rungs 5 to 7 reject everything, so their rows below "
                    "measure nothing")
    print(f"rendered regions: {regions_note}\n")
    print(table(results, RUNG_NAMES, ERROR_CLASSES))

    # Both value families start with "value", so they must not be abbreviated
    # by truncation; the two columns that matter most would read identically.
    labels = (("structural", "struct"),
              ("value_inconsistent", "v-inconsistent"),
              ("value_consistent", "v-CONSISTENT"))
    print("\nSUMMARY BY FAMILY (catch rate, 95% Wilson interval)")
    print("The table above and this block assume the model CITES WHERE IT "
          "LOOKED:\nit points at the box it actually read, which is not the "
          "one it claims.")
    for rung in RUNG_NAMES:
        parts = []
        for fam, short in labels:
            caught, seen = results.family_rate(rung, fam)
            if not seen:
                parts.append(f"{short} -")
                continue
            lo, hi = wilson(caught, seen)
            parts.append(f"{short} {caught / seen * 100:3.0f}% "
                         f"[{lo * 100:.0f}-{hi * 100:.0f}]")
        print(f"  {rung:<16} " + "  ".join(parts))

    print("\nTHE SAME MEASUREMENT, BOTH CITATION ASSUMPTIONS")
    print("Only the grounded rungs can differ; rungs 0-4 never read a "
          "citation.")
    print(f"  {'rung':<16} {'family':<15} {'cites where it looked':>22} "
          f"{'cites true region':>18}")
    for rung in RUNG_NAMES:
        for fam, short in labels:
            a_c, a_s = results.family_rate(rung, fam)
            b_c, b_s = conservative.family_rate(rung, fam)
            if not a_s or a_c == b_c:
                continue
            print(f"  {rung:<16} {short:<15} "
                  f"{a_c / a_s * 100:21.0f}% {b_c / b_s * 100:17.0f}%")
    print("  (rows where the two assumptions agree are omitted)")
    print("  The right-hand column is what this repository published before "
          "2026-08-12.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "adapter": "stub",
            "note": "Measures the ladder, not a model. Error classes are "
                    "constructed, not sampled from real model behavior.",
            "docs_per_class": args.docs,
            "rendered_regions": render_ok,
            # Rung 6's numbers are a direct function of this assumed rate.
            # Recorded so no artifact can be read without it.
            "stub_reread_agreement": stub.reread_agreement,
            # BOTH CITATION ASSUMPTIONS, because rung 5's catch rate is a
            # function of one and an artifact that records only the flattering
            # one is the defect this repository keeps finding in itself.
            "citation_assumption_of_results": "cites where it looked "
                                              "(the box it actually read)",
            "results": results.to_dict(),
            "results_citing_the_true_region": conservative.to_dict(),
        }, indent=2) + "\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
