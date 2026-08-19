#!/usr/bin/env python3
"""Re-grade a saved paid run against the current ladder. Free, no network.

    python scripts/regrade.py audit/degrade_run.json

Why this exists. Rung 7 was added because the degraded-scan run showed the
ladder had no check aimed at the field that actually moved. The honest way to
find out whether it helps is to apply it to THE SAME PAYLOADS the model already
produced, which are recorded in full in audit/*.json. That costs nothing and is
a stronger test than a fresh run, because the payloads were generated before
the rung existed and cannot have been influenced by it.

Any rung added after a run can be evaluated this way. A rung that only looks
good on data collected after someone designed it is not a rung, it is a
hypothesis.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vei.docs import corpus                                    # noqa: E402
from vei.ladder import LADDER, RUNG_NAMES                      # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    data = json.loads(args.run.read_text())
    records = [r for r in data["records"] if "error" not in r]

    # Grouping key: level if the run had levels, otherwise one bucket.
    def level_of(r):
        return r.get("level", "all")

    # The geometry must be rebuilt, not skipped.
    #
    # Rungs 5 and 6 check citations against doc.regions. A Document straight
    # from corpus() has never been rendered, so its regions are empty, and rung
    # 5 skips a field whose true region is unknown rather than failing it.
    # Grading that way silently WEAKENS rungs 5 and 6 instead of erroring: it
    # reports rung 5 catching 1 of 19 at the moderate level where the live run
    # recorded 2. A re-grade that quietly disagrees with the run it is
    # re-grading is worse than no re-grade at all.
    #
    # So each document is re-rendered and re-degraded with the same seed
    # convention degrade_run.py used (seed = index in corpus order), which is
    # deterministic and therefore reproduces the exact regions the live run
    # graded against.
    n = len({r["doc_id"] for r in records})
    base_docs = list(corpus(n))
    levels = sorted({level_of(r) for r in records})
    geom: dict[tuple[str, str], dict] = {}
    if levels != ["all"]:
        from vei.degrade import degrade
        from vei.render import render
        for idx, d in enumerate(base_docs):
            png = render(d)
            for lv in levels:
                _, regions = degrade(png, lv, seed=idx, regions=d.regions)
                geom[(lv, d.doc_id)] = regions
    else:
        from vei.render import render
        for d in base_docs:
            render(d)
            geom[("all", d.doc_id)] = dict(d.regions)

    by_id = {d.doc_id: d for d in base_docs}

    def doc_for(r):
        d = by_id[r["doc_id"]]
        d.regions.clear()
        d.regions.update(geom[(level_of(r), r["doc_id"])])
        return d

    by_level = defaultdict(list)
    for r in records:
        by_level[level_of(r)].append(r)

    print(f"re-grading {len(records)} saved payloads from {args.run}")
    print(f"model: {data.get('model')}   adapter: {data.get('adapter')}")
    print("NOTE: payloads are replayed from disk. No model was called and "
          "nothing was billed.\n")

    out = {}
    for lv, rs in by_level.items():
        errs = [r for r in rs if not r["exact"]]
        oks = [r for r in rs if r["exact"]]
        print(f"-- {lv}: {len(errs)} errors, {len(oks)} correct")
        row = {}
        for name, fn in LADDER:
            caught = 0
            for r in errs:
                if not fn(r["payload"], doc_for(r), r.get("evidence")).accepted:
                    caught += 1
            false_rej = 0
            for r in oks:
                if not fn(r["payload"], doc_for(r), r.get("evidence")).accepted:
                    false_rej += 1
            row[name] = {"caught": caught, "of": len(errs),
                         "false_rejections": false_rej, "of_correct": len(oks)}
            pct = f"{caught / len(errs) * 100:3.0f}%" if errs else "  -"
            print(f"     {name:<16} caught {caught:3d}/{len(errs):<3d} {pct}"
                  f"   false rejections {false_rej}/{len(oks)}")
        out[lv] = row

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "source_run": str(args.run),
            "note": "Re-grade of saved payloads against the current ladder. "
                    "No model was called; nothing was billed. The payloads "
                    "predate rung 7, so they cannot have been influenced by "
                    "its design.",
            "rungs": list(RUNG_NAMES),
            "by_level": out,
        }, indent=2) + "\n")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
