#!/usr/bin/env python3
"""Paid: at what resolution does extraction accuracy fall off, and how?

    ENV_FILE=~/.secrets/ai.env python scripts/dpi_sweep.py
    ENV_FILE=~/.secrets/ai.env python scripts/dpi_sweep.py --confirm

One variable. The four named degradation LEVELS move scale, skew, blur, noise
and JPEG together. That is realistic and it is useless for locating a
threshold: when the error rate jumps between moderate and heavy, nothing in
that experiment says which knob did it. This sweep varies RESOLUTION ALONE,
with every other degradation held at zero, so the answer is attributable.

What it is looking for. The degraded run found a silent zone. A band where the
model is wrong often and the errors are invisible to payload-only checks. The
interesting quantity is not just where accuracy falls but where the ERROR
FAMILY changes: the resolution at which failures stop being consistent-value
(silent) and start being inconsistent (loud, and caught by rung 4).

Paired: the same documents at every resolution.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vei.degrade import resolution_only                        # noqa: E402
from vei.docs import corpus                                    # noqa: E402
from vei.grade import wilson                                   # noqa: E402
from vei.ladder import LADDER                                  # noqa: E402
from vei.models import ClaudeModel                             # noqa: E402
from vei.render import DEFAULT_DPI, render, visual_tokens_for  # noqa: E402
from scripts.real_run import (ANTHROPIC_MODELS, PRICING,       # noqa: E402
                              PRICING_VERIFIED, PROMPT, PROMPT_TOKENS,
                              OUTPUT_TOKENS, classify)
from scripts.degrade_run import family_of                      # noqa: E402

# Concentrated between 90 and 130, because the degraded run already showed
# 150 is clean and 78 is a wipeout. Spending calls confirming the ends again
# would buy nothing; the transition is what is unmeasured.
SWEEP = (150, 130, 120, 110, 100, 90, 80)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=15,
                    help="documents per resolution (default 15)")
    ap.add_argument("--dpi", type=int, nargs="*", default=list(SWEEP))
    ap.add_argument("--model", default="claude-opus-5", choices=ANTHROPIC_MODELS,
                    help="this script calls the Anthropic API only")
    ap.add_argument("--effort", default="medium")
    # Authorized by the operator on 2026-08-11 ("ok to run DPI sweep"), after
    # being deferred and disarmed earlier the same day. The ceiling is 4.00
    # against an estimate of $2.78 for 105 calls, so the guard still stops a
    # run whose estimate has drifted materially above what was authorized: a
    # price change or an --n increase trips it rather than silently spending
    # more. Set back to 0.00 to disarm again.
    ap.add_argument("--max-cost", type=float, default=4.00)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("audit/dpi_sweep.json"))
    ap.add_argument("--overwrite", action="store_true",
                    help="replace an existing --out file; without it a paid run "
                         "refuses before it spends anything")
    args = ap.parse_args()
    # The default --out is an artifact this repository ships. A paid run that
    # wrote over it would replace published evidence with a new sample.
    if args.confirm and args.out.exists() and not args.overwrite:
        print(f"REFUSING TO START: {args.out} exists. Pass another --out, or "
              f"--overwrite to replace it.")
        return 2

    docs = list(corpus(args.n))
    base = {d.doc_id: render(d, dpi=DEFAULT_DPI) for d in docs}

    tokens = {}
    for dpi in args.dpi:
        png, _ = resolution_only(base[docs[0].doc_id], dpi,
                                 regions=docs[0].regions)
        tokens[dpi], _, _ = visual_tokens_for(png)

    p = PRICING[args.model]
    calls = args.n * len(args.dpi)
    tin = sum(args.n * (tokens[d] + PROMPT_TOKENS) for d in args.dpi)
    tout = calls * OUTPUT_TOKENS
    cost = tin / 1e6 * p["in"] + tout / 1e6 * p["out"]

    print(f"model            {args.model}")
    print(f"design           {args.n} documents x {len(args.dpi)} resolutions "
          f"= {calls} calls (paired, resolution is the only variable)")
    for d in args.dpi:
        print(f"  {d:>3} DPI   {tokens[d]:5,} visual tokens")
    print(f"estimated tokens {tin:,} in / {tout:,} out")
    print(f"ESTIMATED COST   ${cost:.2f}  "
          f"(list prices verified {PRICING_VERIFIED})")

    if cost > args.max_cost:
        print(f"\nREFUSING TO START: ${cost:.2f} exceeds --max-cost "
              f"${args.max_cost:.2f}.")
        return 2
    if not args.confirm:
        print("\nDry run. Nothing was sent and nothing was billed.")
        print("Re-run with --confirm to spend the amount above.")
        return 0

    model = ClaudeModel(args.model, effort=args.effort)
    records = []
    spent_in = spent_out = 0
    t0 = time.time()

    for dpi in args.dpi:
        for doc in docs:
            png, regions = resolution_only(base[doc.doc_id], dpi,
                                           regions=doc.regions)
            saved = dict(doc.regions)
            doc.regions.clear()
            doc.regions.update(regions)
            try:
                ex = model.extract(doc, png, prompt=PROMPT)
            except Exception as e:                      # noqa: BLE001
                records.append({"dpi": dpi, "doc_id": doc.doc_id,
                                "error": repr(e)})
                print(f"  {dpi:>3} DPI  {doc.doc_id}  CALL FAILED: {e}")
                doc.regions.clear()
                doc.regions.update(saved)
                continue
            if ex.usage:
                spent_in += ex.usage["input_tokens"]
                spent_out += ex.usage["output_tokens"]
            exact, wrong = classify(ex.payload, doc.truth())
            fam = family_of(ex.payload, doc, wrong)
            verdicts = {name: fn(ex.payload, doc, ex.evidence).accepted
                        for name, fn in LADDER}
            records.append({
                "dpi": dpi, "doc_id": doc.doc_id, "exact": exact,
                "wrong_fields": wrong, "family": fam, "verdicts": verdicts,
                "usage": ex.usage, "payload": ex.payload,
                "evidence": ex.evidence,
            })
            doc.regions.clear()
            doc.regions.update(saved)
            tag = "exact" if exact else f"{fam}: {','.join(wrong[:3])}"
            print(f"  {dpi:>3} DPI  {doc.doc_id}  {tag}")

    elapsed = time.time() - t0
    actual = spent_in / 1e6 * p["in"] + spent_out / 1e6 * p["out"]
    print(f"\n{elapsed:.0f}s   ACTUAL COST ${actual:.2f} "
          f"({spent_in:,} in / {spent_out:,} out) vs ${cost:.2f} estimated")

    by_dpi = defaultdict(list)
    for r in records:
        if "error" not in r:
            by_dpi[r["dpi"]].append(r)

    fams = ("structural", "value_inconsistent", "value_consistent")
    print("\nACCURACY AND ERROR FAMILY BY RESOLUTION")
    print(f"  {'DPI':>4} {'exact':>8} {'error rate':>18}  "
          f"{'struct':>7}{'v-incon':>9}{'v-CONSIST':>11}   "
          f"{'caught':>8} {'quiet':>7}")
    rows = []
    for dpi in args.dpi:
        rs = by_dpi[dpi]
        if not rs:
            continue
        ex_n = sum(1 for r in rs if r["exact"])
        errs = [r for r in rs if not r["exact"]]
        lo, hi = wilson(len(errs), len(rs))
        counts = {f: sum(1 for r in errs if r["family"] == f) for f in fams}
        caught = sum(1 for r in errs if not r["verdicts"]["7_catalog"])
        quiet = len(errs) - caught
        print(f"  {dpi:>4} {ex_n:>4}/{len(rs):<3} "
              f"{len(errs) / len(rs) * 100:>7.0f}% "
              f"[{lo * 100:3.0f}-{hi * 100:3.0f}]  "
              f"{counts[fams[0]]:>7}{counts[fams[1]]:>9}{counts[fams[2]]:>11}   "
              f"{caught:>8} {quiet:>7}")
        rows.append({"dpi": dpi, "n": len(rs), "exact": ex_n,
                     "errors": len(errs), "families": counts,
                     "caught_by_full_ladder": caught, "uncaught": quiet})

    print("\n'quiet' = wrong AND accepted by every rung including catalog. "
          "That is the number\nthat matters: an error nothing downstream can "
          "see.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "adapter": model.adapter, "model": args.model, "effort": args.effort,
        "design": "paired: same documents at every resolution; resolution is "
                  "the only variable, all other degradation held at zero",
        "n_per_dpi": args.n, "dpi": list(args.dpi),
        "visual_tokens": tokens,
        "elapsed_s": round(elapsed, 1),
        "pricing_verified": PRICING_VERIFIED,
        "estimated_cost_usd": round(cost, 4),
        "actual_cost_usd": round(actual, 4),
        "tokens": {"input": spent_in, "output": spent_out},
        "summary": rows,
        "records": records,
    }, indent=2) + "\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
