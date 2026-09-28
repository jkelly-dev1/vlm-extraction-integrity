#!/usr/bin/env python3
"""Paid: does scan quality change the error rate, and which family breaks?

    ENV_FILE=~/.secrets/ai.env python scripts/degrade_run.py
    ENV_FILE=~/.secrets/ai.env python scripts/degrade_run.py --confirm

Paired design. The SAME documents are sent at every degradation level, so a
difference between levels is a difference in the paper and not in the sample.
An unpaired design would confound "heavy scans are harder" with "these
happened to be harder documents", which at n=25 per level is a very live risk.

Why this run exists. The clean probe returned 38 correct extractions out of
38. That is the easy case. This run asks whether the failure mode appears when
the page looks like something a business actually receives, and, critically,
WHICH FAMILY the failures fall into. A degraded page producing structural
errors would mean schema validation is enough after all. A degraded page
producing consistent-value errors is the case the whole ladder is built for.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vei.degrade import LEVEL_NAMES, LEVELS, degrade, effective_dpi  # noqa: E402
from vei.docs import corpus                                    # noqa: E402
from vei.grade import wilson                                   # noqa: E402
from vei.ladder import LADDER                                 # noqa: E402
from vei.models import ClaudeModel                             # noqa: E402
from vei.render import DEFAULT_DPI, render, visual_tokens_for  # noqa: E402
from scripts.real_run import (ANTHROPIC_MODELS, PRICING,       # noqa: E402
                              PRICING_VERIFIED, PROMPT, PROMPT_TOKENS,
                              OUTPUT_TOKENS, classify)


def family_of(payload: str, doc, wrong: list[str]) -> str:
    """Which family does an observed error fall into?

    This is the measurement the run exists for, so it is decided by running
    the payload through the ladder rather than by inspecting the field names:

      structural          rejected at rung 3 or earlier
      value_inconsistent  survives the schema, fails cross-field arithmetic
      value_consistent    survives every payload-only rung and is STILL wrong

    The third case is the one no schema can reach.
    """
    if not wrong:
        return "correct"
    rungs = dict(LADDER)
    if not rungs["3_strict_schema"](payload, doc, None).accepted:
        return "structural"
    if not rungs["4_cross_field"](payload, doc, None).accepted:
        return "value_inconsistent"
    return "value_consistent"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25,
                    help="documents per level (default 25)")
    ap.add_argument("--model", default="claude-opus-5", choices=ANTHROPIC_MODELS,
                    help="this script calls the Anthropic API only")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--max-cost", type=float, default=3.50)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("audit/degrade_run.json"))
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

    # Measure real pages at each level instead of assuming a size.
    docs = list(corpus(args.n))
    base = {d.doc_id: render(d, dpi=args.dpi) for d in docs}
    per_level_tokens = {}
    for lv in LEVEL_NAMES:
        png, _ = degrade(base[docs[0].doc_id], lv, seed=0,
                         regions=docs[0].regions)
        per_level_tokens[lv], _, _ = visual_tokens_for(png)

    p = PRICING[args.model]
    tin = sum(args.n * (per_level_tokens[lv] + PROMPT_TOKENS)
              for lv in LEVEL_NAMES)
    tout = args.n * len(LEVEL_NAMES) * OUTPUT_TOKENS
    cost = tin / 1e6 * p["in"] + tout / 1e6 * p["out"]
    calls = args.n * len(LEVEL_NAMES)

    print(f"model            {args.model}")
    print(f"design           {args.n} documents x {len(LEVEL_NAMES)} levels "
          f"= {calls} calls (paired)")
    for lv in LEVEL_NAMES:
        print(f"  {lv:<9} {per_level_tokens[lv]:5,} visual tokens  "
              f"eff {effective_dpi(lv):5.1f} DPI   {LEVELS[lv].description}")
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

    for lv in LEVEL_NAMES:
        for idx, doc in enumerate(docs):
            # Degrade from the SAME base render, and take the transformed
            # regions. Grading grounding against the clean coordinates would
            # be grading against a different image.
            #
            # The seed is the document INDEX, not hash(doc_id): Python
            # randomizes string hashing per process, so a hash-derived seed
            # would give a different skew angle on every run and the paired
            # design would silently stop being reproducible.
            png, regions = degrade(base[doc.doc_id], lv, seed=idx,
                                   regions=doc.regions)
            saved = dict(doc.regions)
            doc.regions.clear()
            doc.regions.update(regions)
            try:
                ex = model.extract(doc, png, prompt=PROMPT)
            except Exception as e:                      # noqa: BLE001
                records.append({"level": lv, "doc_id": doc.doc_id,
                                "error": repr(e)})
                print(f"  {lv:<9} {doc.doc_id}  CALL FAILED: {e}")
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
                "level": lv, "doc_id": doc.doc_id, "exact": exact,
                "wrong_fields": wrong, "family": fam, "verdicts": verdicts,
                "usage": ex.usage, "payload": ex.payload,
                "evidence": ex.evidence,
            })
            doc.regions.clear()
            doc.regions.update(saved)
            tag = "exact" if exact else f"{fam}: {','.join(wrong[:3])}"
            print(f"  {lv:<9} {doc.doc_id}  {tag}")

    elapsed = time.time() - t0
    actual = spent_in / 1e6 * p["in"] + spent_out / 1e6 * p["out"]

    print(f"\n{elapsed:.0f}s   ACTUAL COST ${actual:.2f} "
          f"({spent_in:,} in / {spent_out:,} out) vs ${cost:.2f} estimated")

    print("\nERROR RATE BY LEVEL (95% Wilson)")
    print(f"  {'level':<9} {'eff DPI':>8} {'exact':>9} {'error rate':>22}")
    by_level = defaultdict(list)
    for r in records:
        if "error" not in r:
            by_level[r["level"]].append(r)
    for lv in LEVEL_NAMES:
        rs = by_level[lv]
        if not rs:
            continue
        ex_n = sum(1 for r in rs if r["exact"])
        err = len(rs) - ex_n
        lo, hi = wilson(err, len(rs))
        print(f"  {lv:<9} {effective_dpi(lv):8.1f} {ex_n:5d}/{len(rs):<3d} "
              f"{err / len(rs) * 100:8.0f}% [{lo * 100:3.0f}-{hi * 100:3.0f}]")

    print("\nWHICH FAMILY DID THE ERRORS FALL INTO")
    fams = ("structural", "value_inconsistent", "value_consistent")
    print(f"  {'level':<9} " + "".join(f"{f:>20}" for f in fams))
    for lv in LEVEL_NAMES:
        rs = by_level[lv]
        if not rs:
            continue
        counts = {f: sum(1 for r in rs if r["family"] == f) for f in fams}
        print(f"  {lv:<9} " + "".join(f"{counts[f]:>20}" for f in fams))

    print("\nWHAT THE LADDER CAUGHT (of the errors at each level)")
    for lv in LEVEL_NAMES:
        rs = [r for r in by_level[lv] if not r["exact"]]
        clean_rs = [r for r in by_level[lv] if r["exact"]]
        if not by_level[lv]:
            continue
        parts = []
        for name, _ in LADDER:
            caught = sum(1 for r in rs if not r["verdicts"][name])
            false_r = sum(1 for r in clean_rs if not r["verdicts"][name])
            parts.append(f"{name.split('_')[0]}:{caught}/{len(rs)}"
                         f"(fr{false_r})")
        print(f"  {lv:<9} " + " ".join(parts))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "adapter": model.adapter, "model": args.model, "effort": args.effort,
        "design": "paired: same documents at every level",
        "n_per_level": args.n, "levels": list(LEVEL_NAMES),
        "level_specs": {lv: {"effective_dpi": effective_dpi(lv),
                             "visual_tokens": per_level_tokens[lv],
                             "description": LEVELS[lv].description}
                        for lv in LEVEL_NAMES},
        "elapsed_s": round(elapsed, 1),
        "pricing_verified": PRICING_VERIFIED,
        "estimated_cost_usd": round(cost, 4),
        "actual_cost_usd": round(actual, 4),
        "tokens": {"input": spent_in, "output": spent_out},
        "records": records,
    }, indent=2) + "\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
