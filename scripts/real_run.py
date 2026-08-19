#!/usr/bin/env python3
"""The paid half: ask a real VLM to extract, and grade what comes back.

    ENV_FILE=~/.secrets/ai.env python scripts/real_run.py --probe
    ENV_FILE=~/.secrets/ai.env python scripts/real_run.py --probe --confirm

WITHOUT --confirm THIS SPENDS NOTHING. The default prints the plan and the
cost estimate and exits. That is deliberate: a script that bills an account
because someone ran it to see what it did is a badly built script.

What the paid run measures, and what the offline run does not. The offline
demo measures the LADDER against constructed errors: given an error of class
C, does rung N reject it? Exact, free, and says nothing about how often a
model commits C. This run measures the other half: the observed rate at
which a real model, reading a real rendered page, gets a field wrong, and
which family that error falls in. The two numbers multiply; neither is the
headline on its own, and reporting one as the other is the easiest way to
mislead with this repository.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vei.docs import corpus                                    # noqa: E402
from vei.grade import Results, wilson                          # noqa: E402
from vei.ladder import LADDER, RUNG_NAMES                      # noqa: E402
from vei.models import ClaudeModel                             # noqa: E402
from vei.render import (DEFAULT_DPI, RENDER_REVISION, render,  # noqa: E402
                        visual_tokens_for)

# Anthropic list prices, USD per million tokens. Verified 2026-08-10 against
# the published pricing page. They are a dated claim, not a constant, if
# this file is being read long after that date, re-check before trusting the
# estimate, and note that any promotional rate may have lapsed. List prices,
# USD per million tokens, re-verified 2026-08-11 by reading
# platform.claude.com/docs/en/about-claude/pricing directly.
#
# The claude-sonnet-5 entry was wrong here until 2026-08-11: it carried $3.00 /
# $15.00, the price that had been SCHEDULED to take effect on 2026-09-01 when
# the $2/$10 launch pricing expired. That increase was canceled and $2/$10
# became the standard price, so this table was over-estimating Sonnet by 50%.
# No published figure in this repository is affected, every paid run used
# claude-opus-5, whose $5/$25 is confirmed unchanged, but the failure mode is
# worth naming: a canceled price increase is the least visible way for a
# hard-coded price to go stale. Nothing errors, nothing warns, and the number
# simply stops being true.
PRICING = {
    # Anthropic
    "claude-opus-5":   {"in": 5.00, "out": 25.00},
    "claude-sonnet-5": {"in": 2.00, "out": 10.00},
    "claude-haiku-4-5": {"in": 1.00, "out": 5.00},
    # OpenAI. gpt-5.6-sol is the price-matched counterpart to claude-opus-5:
    # identical input price, 20% more on output. Both accept image input.
    # Verified on the model pages, NOT from the pricing table, whose "images"
    # column means something else and reads as "no" for the whole GPT-5 family.
    "gpt-5.6-sol":     {"in": 5.00, "out": 30.00},
    "gpt-5.4":         {"in": 2.50, "out": 15.00},
    "gpt-5.4-mini":    {"in": 0.75, "out": 4.50},
    "gpt-4o":          {"in": 2.50, "out": 10.00},
}
PRICING_VERIFIED = "2026-08-11"

# Which adapter serves a model id. Kept next to PRICING so a model can never be
# priced without also being routable, which is how a cross-provider run ends up
# quietly billing the wrong provider's rates.
PROVIDER = {m: ("openai" if m.startswith(("gpt-", "o1", "o3", "o4"))
                else "anthropic")
            for m in PRICING}

# OPENAI long-context surcharge, recorded because it does not apply to this
# workload and someone will eventually reuse this table where it does: prompts
# over 272K input tokens are billed at 2x input and 1.5x output for the full
# request, not just the excess. This repo's pages are ~800 input tokens, three
# orders of magnitude below that, so the flat rates above are correct here.
# Anthropic has no equivalent surcharge: Claude 4.6+ includes the full 1M
# window at standard pricing. Both figures were read off the providers'
# published pricing pages on the date recorded beside the rate tables, not from
# memory, and both should be re-read before being quoted anywhere.

# Measured from the prompt below plus a full response; deliberately rounded up
# so the estimate errs high rather than low.
PROMPT_TOKENS = 420

# Output tokens per call, by provider, and why this is not one number. It was
# 900 for both until 2026-08-12, when a gpt-5.4 run of 80 calls was quoted at
# $1.30 and billed $1.69: 1,270 output tokens per call against the 900
# assumed. On the Responses API a reasoning model's thinking tokens are billed
# as output and counted against max_output_tokens, so the same page costs more
# output there than on the Anthropic side even when the JSON is identical.
# Measured per call at effort=medium: opus-5 812, sonnet-5 992, gpt-5.6-sol
# 981, gpt-5.4 1,270. The figures below round above every one of those,
# because an estimate that errs LOW is the one that spends money nobody
# approved.
OUTPUT_TOKENS_BY_PROVIDER = {"anthropic": 1050, "openai": 1350}
OUTPUT_TOKENS = OUTPUT_TOKENS_BY_PROVIDER["anthropic"]

PROMPT = """\
Extract this purchase order as a single JSON object and nothing else.

Fields: po_number, order_date (YYYY-MM-DD), due_date (YYYY-MM-DD), vendor,
terms, currency, line_items, subtotal_cents, tax_cents, total_cents.

Each line item: row, sku, description, quantity, unit_price_cents,
amount_cents. All money is an INTEGER NUMBER OF CENTS, never a string and
never a float.

Also include two extra keys used for verification:
  "row_count": the number of line item rows printed in the table.
  "citations": an object mapping each of "po_number", "subtotal_cents" and
    "total_cents" to [x0, y0, x1, y1] pixel bounds of where you read it.

Report what is printed on the page. Do not correct arithmetic you believe is
wrong, and do not omit a row you are unsure about."""


def estimate(n: int, model: str, vis: int) -> tuple[float, int, int]:
    """Cost estimate from the MEASURED visual-token count of a real page."""
    tin = n * (vis + PROMPT_TOKENS)
    tout = n * OUTPUT_TOKENS_BY_PROVIDER[PROVIDER[model]]
    p = PRICING[model]
    return (tin / 1e6 * p["in"] + tout / 1e6 * p["out"], tin, tout)


def classify(payload: str, truth: dict) -> tuple[bool, list[str]]:
    """Compare an extraction against ground truth. Returns (exact, wrong)."""
    try:
        got = json.loads(payload)
    except json.JSONDecodeError:
        return False, ["<unparseable>"]
    wrong = []
    for k, want in truth.items():
        if k == "line_items":
            rows = got.get("line_items")
            if not isinstance(rows, list) or len(rows) != len(want):
                wrong.append("line_items:count")
                continue
            for i, (g, w) in enumerate(zip(rows, want)):
                for f, wv in w.items():
                    if not isinstance(g, dict) or g.get(f) != wv:
                        wrong.append(f"line_items[{i}].{f}")
        elif got.get(k) != want:
            wrong.append(k)
    return not wrong, wrong


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true",
                    help="small calibration run (default 40 documents)")
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--model", default="claude-opus-5", choices=sorted(PRICING))
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--effort", default="medium")
    ap.add_argument("--max-cost", type=float, default=2.00,
                    help="refuse to start if the estimate exceeds this (USD)")
    ap.add_argument("--confirm", action="store_true",
                    help="actually spend money; without it this is a dry run")
    ap.add_argument("--out", type=Path, default=Path("audit/real_run.json"))
    args = ap.parse_args()

    n = args.n if args.n is not None else (40 if args.probe else 120)

    # Render one page for real and measure it, rather than assuming a size.
    # This costs nothing and is the difference between an estimate and a guess.
    sample = next(iter(corpus(1)))
    vis, vw, vh = visual_tokens_for(render(sample, dpi=args.dpi))
    cost, tin, tout = estimate(n, args.model, vis)

    print(f"model            {args.model}")
    print(f"documents        {n}   ({args.dpi} DPI, {vw}x{vh} px after crop, "
          f"{vis:,} visual tokens/page)")
    print(f"estimated tokens {tin:,} in / {tout:,} out")
    print(f"ESTIMATED COST   ${cost:.2f}  "
          f"(list prices verified {PRICING_VERIFIED})")

    if cost > args.max_cost:
        print(f"\nREFUSING TO START: ${cost:.2f} exceeds the --max-cost "
              f"ceiling of ${args.max_cost:.2f}.")
        return 2
    if not args.confirm:
        print("\nDry run. Nothing was sent and nothing was billed.")
        print("Re-run with --confirm to spend the amount above.")
        return 0

    model = ClaudeModel(args.model, effort=args.effort)
    results = Results()
    records = []
    spent_in = spent_out = 0
    t0 = time.time()

    for i, doc in enumerate(corpus(n)):
        png = render(doc, dpi=args.dpi)
        try:
            ex = model.extract(doc, png, prompt=PROMPT)
        except Exception as e:                      # noqa: BLE001
            # A failed call is data, not a reason to lose the calls already
            # paid for. Record it and keep going.
            records.append({"doc_id": doc.doc_id, "error": repr(e)})
            print(f"  {doc.doc_id}  CALL FAILED: {e}")
            continue

        if ex.usage:
            spent_in += ex.usage["input_tokens"]
            spent_out += ex.usage["output_tokens"]

        exact, wrong = classify(ex.payload, doc.truth())
        ev = dict(ex.evidence)
        verdicts = {}
        for name, fn in LADDER:
            v = fn(ex.payload, doc, ev)
            verdicts[name] = {"accepted": v.accepted, "reason": v.reason}
            # An extraction that matches truth is the "clean" case for the
            # false-rejection measure; anything else is a real observed error.
            results.record(name, None if exact else "observed_error",
                           not v.accepted)

        records.append({
            "doc_id": doc.doc_id, "exact": exact, "wrong_fields": wrong,
            "verdicts": verdicts, "usage": ex.usage,
            "payload": ex.payload, "evidence": ev,
        })
        note = "exact" if exact else "WRONG: " + ",".join(wrong[:4])
        print(f"  {doc.doc_id}  {note}")

    elapsed = time.time() - t0
    p = PRICING[args.model]
    actual = spent_in / 1e6 * p["in"] + spent_out / 1e6 * p["out"]
    ok = [r for r in records if "error" not in r]
    exact_n = sum(1 for r in ok if r["exact"])

    print(f"\n{len(ok)}/{n} calls succeeded in {elapsed:.0f}s")
    print(f"exact extractions {exact_n}/{len(ok)}")
    if ok:
        lo, hi = wilson(len(ok) - exact_n, len(ok))
        print(f"observed error rate {(len(ok) - exact_n) / len(ok) * 100:.0f}% "
              f"[{lo * 100:.0f}-{hi * 100:.0f}] (95% Wilson)")
    print(f"ACTUAL COST ${actual:.2f} "
          f"({spent_in:,} in / {spent_out:,} out) vs ${cost:.2f} estimated")

    print("\nOf the extractions that were wrong, which rung rejected them:")
    for rung in RUNG_NAMES:
        cell = results.by_class.get(rung, {}).get("observed_error")
        clean_cell = results.clean.get(rung)
        c = f"{cell.caught}/{cell.seen}" if cell and cell.seen else "0/0"
        fr = (f"{clean_cell.caught}/{clean_cell.seen}"
              if clean_cell and clean_cell.seen else "0/0")
        print(f"  {rung:<16} caught {c:>8}   false rejections {fr:>8}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "adapter": "claude",
        "model": args.model,
        "effort": args.effort,
        "dpi": args.dpi,
        # Which drawing of the page this was measured on. Clean pages are
        # byte-identical across the 2026-08-11 and 2026-08-12 revisions, only a
        # long terms value is drawn differently, but an artifact that does not
        # say which page it saw cannot be compared to one that does.
        "render_revision": RENDER_REVISION,
        "n": n,
        "elapsed_s": round(elapsed, 1),
        "pricing_verified": PRICING_VERIFIED,
        "estimated_cost_usd": round(cost, 4),
        "actual_cost_usd": round(actual, 4),
        "tokens": {"input": spent_in, "output": spent_out},
        "exact": exact_n,
        "succeeded": len(ok),
        "note": "Observed error rates for a real model. The offline demo's "
                "catch rates measure the ladder against constructed errors; "
                "these two must not be combined into a single figure.",
        "results": results.to_dict(),
        "records": records,
    }, indent=2) + "\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
