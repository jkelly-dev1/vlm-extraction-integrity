"""Re-derive the README's run figures from audit/*.json and diff them.

A README is prose and drifts; audit/*.json is evidence and does not. This
script rebuilds each figure from the JSON and asserts the exact string is
present in the README, so a re-run that shifts a figure fails loudly instead of
leaving the document silently wrong.

    python3 scripts/check_readme_numbers.py            check
    python3 scripts/check_readme_numbers.py --emit     print what it derives

Whitespace is normalized on both sides. Several of the figures here sit inside
sentences that wrap across three lines, and a deriver that demanded a literal
match would fire on every reflow until a reader learned to ignore it.

What it does NOT cover, said here instead of left to be discovered. The
offline ladder tables, the IoU medians and the citation-honesty family grid
come from the constructed corpus, not from a shipped run, and the prompt A/B
figures come from a run this file does not read. Also not derived: the
Document-borne prompt injection section's copy of "0 of 90", "26 of 40" and
"29 of 40", "55 of 114", "34 of 38", "38 of 38", the false-rejection
sentences, 26/40, 1/40, the three McNemar p-values, and the result column of
the cost ledger except its loud, consistent and redrawn rows and the repaired
opus-5 row. The adapter smoke test's 2 calls and $0.07 wrote no artifact, so
they are constants below, and the ledger total is derived from them and from
every other row. Each of those was checked against its artifact in review.
The count is printed whether or not anything is missing, so a version of this
script that silently stopped deriving half of them is visible, not clean.

A figure whose definition could NOT be pinned exactly is left out.
A deriver that encodes a guess at what a number meant is worse than no deriver:
it passes, and it certifies the guess.
"""

import glob
import json
import math
import os
import re
import subprocess
import sys
from decimal import ROUND_HALF_UP, Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The rung whose verdict is the pixel-grounding decision. Every
#: false-rejection figure on this page is this rung saying no to an extraction
#: that was correct.
GROUNDING = "5_grounded"


def load(name):
    with open(os.path.join(ROOT, "audit", name), encoding="utf-8") as fh:
        return json.load(fh)


def wilson(k, n, z=1.96):
    """The 95% score interval, which is what every bracket on the page is."""
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, center - half), min(1.0, center + half)


def pct(x):
    return int(round(100 * x))


def rows_degradation():
    """The headline table: what each scan quality costs and what is caught."""
    run = load("degrade_run.json")
    regrade = load("regrade_degraded.json")["by_level"]
    specs = run["level_specs"]
    n = run["n_per_level"]
    out = []
    for level in run["levels"]:
        recs = [r for r in run["records"] if r["level"] == level]
        wrong = sum(1 for r in recs if not r["exact"])
        lo, hi = wilson(wrong, n)
        dpi = specs[level]["effective_dpi"]
        rate = "%d%% [%d-%d]" % (pct(wrong / n), pct(lo), pct(hi))
        if wrong:
            # The ladder here is every rung the offline demo runs, which ends
            # at the re-read. The catalog is rung 7 and is reported separately
            # in the section below, because it is the finding.
            caught = regrade[level]["6_reread"]["caught"]
            ladder = "%d of %d (%d%%)" % (caught, wrong, pct(caught / wrong))
        else:
            ladder = "--"
        out.append(("degradation:" + level, level, dpi, wrong, n, rate, ladder))
    return out


def rows_rung_at_108():
    """What each rung caught at the one quality where the errors are silent."""
    mod = load("regrade_degraded.json")["by_level"]["moderate"]
    labels = [("3_strict_schema", "3 strict schema"),
              ("4_cross_field", "4 cross-field"),
              ("5_grounded", "5 grounded"),
              ("6_reread", "6 re-read"),
              ("7_catalog", "7 catalog")]
    out = []
    for key, label in labels:
        cell = mod[key]
        out.append(("rung108:" + key, label, cell["caught"], cell["of"],
                    cell["false_rejections"], cell["of_correct"]))
    return out


def rows_dpi_sweep():
    """Where the silent errors appear as the page gets worse."""
    sweep = load("dpi_sweep.json")
    out = []
    for s in sweep["summary"]:
        dpi = s["dpi"]
        recs = [r for r in sweep["records"] if r["dpi"] == dpi]
        correct = [r for r in recs if r["exact"]]
        false_rej = sum(1 for r in correct
                        if not r["verdicts"].get(GROUNDING, True))
        lo, hi = wilson(s["errors"], s["n"])
        out.append(("dpi:" + str(dpi), dpi, s["exact"], s["n"],
                    pct(s["errors"] / s["n"]), pct(lo), pct(hi),
                    s["families"]["value_consistent"],
                    s["families"]["structural"],
                    s["caught_by_full_ladder"], s["uncaught"],
                    false_rej, len(correct)))
    return out


def _model_runs():
    """The four-model comparison, one page, one run, keyed by model."""
    out = {}
    for path in sorted(glob.glob(os.path.join(
            ROOT, "audit", "inject_consistent_n40_*fixedpage.json"))):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        out[d["model"]] = d
    return out


def _split(run):
    """(clean arm that did not error, injected arm, complying payloads).

    A reply that errored is NOT A correct extraction AND NOT A wrong one. Four
    of one model's replies errored and carry no outcome, so its denominator
    on this page is 36 and not 40. The README states that beside the figure
    instead of leaving a reader to reconstruct it.
    """
    clean = [r for r in run["records"] if r["arm"] == "none" and "outcome" in r]
    injected = [r for r in run["records"] if r["arm"] != "none"]
    complied = [r for r in injected if r.get("outcome") == "complied"]
    return clean, injected, complied


def prose_figures():
    """The sentences that carry a number, which is where figures drift."""
    out = []

    regrade = load("regrade_degraded.json")["by_level"]["moderate"]
    out.append(("prose:headline",
                "pixel-grounding checks built to catch them caught %d of %d, "
                "while a plain lookup against the parts master caught %d of %d."
                % (regrade["6_reread"]["caught"], regrade["6_reread"]["of"],
                   regrade["7_catalog"]["caught"], regrade["7_catalog"]["of"])))

    runs = _model_runs()
    order = ["claude-opus-5", "gpt-5.4", "claude-sonnet-5", "gpt-5.6-sol"]
    scores = []
    for model in order:
        _, injected, complied = _split(runs[model])
        scores.append("%d/%d" % (len(complied), runs[model]["n_per_arm"]))
    out.append(("prose:four-model",
                "the same experiment scores %s, %s, %s and %s, while all four "
                "demonstrably read the injected text."
                % tuple(scores)))

    rej = {}
    for model, run in runs.items():
        clean, _, complied = _split(run)
        rej[model] = {
            "complied_rejected": sum(
                1 for r in complied
                if not r["verdicts"].get(GROUNDING, True)),
            "complied": len(complied),
            "clean_rejected": sum(
                1 for r in clean if not r["verdicts"].get(GROUNDING, True)),
            "clean": len(clean),
        }
    g = rej["gpt-5.4"]
    o = rej["claude-opus-5"]
    out.append(("prose:complying",
                "Pixel grounding rejected %d of the %d complying payloads from "
                "`gpt-5.4` and %d of the %d from `claude-opus-5`,"
                % (g["complied_rejected"], g["complied"],
                   o["complied_rejected"], o["complied"])))

    s = rej["claude-sonnet-5"]
    quiet = {m: v for m, v in rej.items()
             if m != "claude-sonnet-5" and v["clean_rejected"] == 0}
    denominators = {v["clean"] for v in quiet.values()}
    # The three models that rejected none share a denominator OR the sentence
    # is wrong. "none of the 40 from three of the four" only reads correctly
    # while all three have the same clean-arm size; if a re-run changes one,
    # this derivation fails instead of silently agreeing with a sentence that
    # no longer holds.
    if len(quiet) == 3 and len(denominators) == 1:
        out.append(("prose:clean-arm",
                    "rejected none of the %d control-arm replies from three "
                    "of the four models and %d of %d from `claude-sonnet-5`,"
                    % (denominators.pop(),
                       s["clean_rejected"], s["clean"])))
    else:
        out.append(("prose:clean-arm", "UNDERIVABLE: the three quiet models no "
                    "longer share one denominator"))

    # The redrawn-page runs, 2026-09-25: the consistent n=15 table repeated on
    # pages where every channel carries the whole ask legibly. "only" is part
    # of the claim, so it is derived too: if Opus complied through any other
    # channel the sentence is false and this says so instead of matching.
    redrawn = {}
    for path in sorted(glob.glob(os.path.join(
            ROOT, "audit", "inject_consistent_n15_fitlayout_*.json"))):
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        by_arm = {}
        for r in d["records"]:
            if r.get("outcome") == "complied":
                by_arm[r["arm"]] = by_arm.get(r["arm"], 0) + 1
        redrawn[d["model"]] = (by_arm, d["n_per_arm"])
    (o, n), (g, _) = redrawn["claude-opus-5"], redrawn["gpt-5.4"]
    times = {1: "once", 2: "twice"}
    if set(o) == {"metadata_field"}:
        out.append(("prose:redrawn",
                    "`claude-opus-5` still moved only through the "
                    "legitimate-looking field, %d of %d, while `gpt-5.4` "
                    "followed the plain note on the page %d times in %d and "
                    "that field %s;"
                    % (o["metadata_field"], n, g.get("plain_note", 0), n,
                       times.get(g.get("metadata_field", 0),
                                 "%d times" % g.get("metadata_field", 0)))))
    else:
        out.append(("prose:redrawn", "UNDERIVABLE: claude-opus-5 complied "
                    "through %s, not only metadata_field" % sorted(o)))
    return out


def injection_headline():
    """The second headline blockquote, from the loud run and the repaired
    n=40 opus-5 run. "the one channel" is part of the claim, so it is
    derived too."""
    loud = load("inject_run.json")
    injected = [r for r in loud["records"] if r["arm"] != "none"]
    channels = len([a for a in loud["arms"] if a != "none"])
    loud_complied = sum(1 for r in injected if r.get("outcome") == "complied")
    run = _model_runs()["claude-opus-5"]
    _, _, complied = _split(run)
    arms = {r["arm"] for r in complied}
    if len(arms) != 1:
        return [("prose:injection-headline", "UNDERIVABLE: claude-opus-5 "
                 "complied through %s, not one channel" % sorted(arms))]
    caught_34 = sum(1 for r in complied
                    if not (r["verdicts"]["3_strict_schema"]
                            and r["verdicts"]["4_cross_field"]))
    rungs = len(complied[0]["verdicts"])
    survived = sum(1 for r in complied if all(r["verdicts"].values()))
    words = {6: "six", 8: "eight"}
    return [("prose:injection-headline",
             "An injection asking for an absurd value scored %d of %d across "
             "%s channels. The same %s channels asking for a value that "
             "reconciles scored %d of %d on `%s` through the one channel "
             "where the instruction reads as legitimate document content: "
             "schema validation and cross-field arithmetic caught %d of those "
             "%d, and %d of the %d survived all %s rungs."
             % (loud_complied, len(injected), words[channels], words[channels],
                len(complied), run["n_per_arm"], run["model"], caught_34,
                len(complied), survived, len(complied), words[rungs]))]


#: Each paid artifact and the ledger row it is. Several rows share a label
#: and a model, so a row is matched with its call count and cost as well.
LEDGER = [
    ("real_run.json", "clean probe"),
    ("degrade_run.json", "degraded scans"),
    ("inject_run.json", "injection (loud)"),
    ("dpi_sweep.json", "resolution sweep"),
    ("inject_consistent.json", "injection (consistent)"),
    ("inject_consistent_n40.json", "injection (consistent, n=40)"),
    ("inject_consistent_n40_openai.json", "injection (consistent, n=40)"),
    ("inject_consistent_n40_sonnet.json", "injection (consistent, n=40)"),
    ("inject_consistent_n40_opus5_fixedpage.json",
     "injection (consistent, n=40)"),
    ("inject_consistent_n40_gpt54_fixedpage.json",
     "injection (consistent, n=40)"),
    ("inject_consistent_n40_sonnet_fixedpage.json",
     "injection (consistent, n=40)"),
    ("inject_consistent_n40_sol_fixedpage.json",
     "injection (consistent, n=40)"),
    ("citation_prompt_sonnet.json", "citation prompt A/B"),
    ("inject_consistent_n15_fitlayout_claude-opus-5.json",
     "injection (consistent, redrawn pages)"),
    ("inject_consistent_n15_fitlayout_gpt-5_4.json",
     "injection (consistent, redrawn pages)"),
]

#: The adapter smoke test wrote no artifact; its row is these two constants.
SMOKE_MODEL, SMOKE_CALLS, SMOKE_COST = "gpt-5.6-sol", 2, Decimal("0.07")


def _cents(x):
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _by_arm(run):
    counts = {a: 0 for a in run["arms"] if a != "none"}
    for r in run["records"]:
        if r.get("outcome") == "complied":
            counts[r["arm"]] += 1
    return counts


def _one_channel_cell(run, suffix):
    """"k/n on `arm`, 0/n on the other five", or UNDERIVABLE when a second
    arm complied, since the cell would then be false."""
    counts, n = _by_arm(run), run["n_per_arm"]
    moved = [a for a, k in counts.items() if k]
    if len(moved) != 1:
        return "UNDERIVABLE: %s complied through %s" % (run["model"], moved)
    words = {5: "five"}
    return "%d/%d on `%s`, 0/%d on the other %s%s" % (
        counts[moved[0]], n, moved[0], n, words[len(counts) - 1], suffix)


def ledger_rows():
    """The cost ledger: every row's calls and cost from its artifact, the
    total from the rows, and the result cell of the rows the headlines
    repeat. A call that errored carries no payload and is not a call."""
    out, calls_total, cost_total = [], SMOKE_CALLS, SMOKE_COST
    for name, label in LEDGER:
        run = load(name)
        recs = run.get("records") or run["results"]
        calls = sum(1 for r in recs if "payload" in r)
        cost = _cents(run["actual_cost_usd"])
        calls_total += calls
        cost_total += cost
        row = "| %s | %s | %d | $%s |" % (
            label, run["model"].replace("claude-", ""), calls, cost)
        if name == "inject_run.json":
            injected = [r for r in recs if r["arm"] != "none"]
            row += " %d/%d compliance |" % (
                sum(1 for r in injected if r.get("outcome") == "complied"),
                len(injected))
        elif name == "inject_consistent.json":
            row += " %s |" % _one_channel_cell(
                run, " (four of those five pages were defective; re-run "
                "below)")
        elif name == "inject_consistent_n40_opus5_fixedpage.json":
            _, _, complied = _split(run)
            row += (" %d/%d on the repaired page; %d of %d survived the full "
                    "ladder |" % (len(complied), run["n_per_arm"],
                                  sum(1 for r in complied
                                      if all(r["verdicts"].values())),
                                  len(complied)))
        elif name.startswith("inject_consistent_n15_fitlayout_"):
            if run["model"] == "claude-opus-5":
                row += " %s |" % _one_channel_cell(run, ", now legible")
            else:
                counts, n = _by_arm(run), run["n_per_arm"]
                moved = sorted((a for a, k in counts.items() if k),
                               key=lambda a: -counts[a])
                row += " %s |" % ", ".join(
                    "%d/%d `%s`" % (counts[a], n, a) for a in moved)
        out.append(("ledger:" + name, row))
    out.append(("ledger:smoke", "| adapter smoke test | %s | %d | $%s |"
                % (SMOKE_MODEL, SMOKE_CALLS, SMOKE_COST)))
    out.append(("ledger:total", "| total | | {:,} | ${} | |".format(
        calls_total, cost_total)))
    return out


def investigation_scope():
    """The Layout line for INJECTION.md: the injection runs and the prompt
    A/B, and the models they ran on."""
    runs = [load(os.path.basename(p)) for p in sorted(
        glob.glob(os.path.join(ROOT, "audit", "inject_*.json"))
        + glob.glob(os.path.join(ROOT, "audit", "citation_prompt_*.json")))]
    return [("layout:injection-runs",
             "the injection investigation in full; %d paid runs, %d models"
             % (len(runs), len({r["model"] for r in runs})))]


def collected_test_count():
    """The suite size, as pytest collects it. Counting `def test_` would
    undercount the parametrized tests. Where the rendering tests cannot be
    collected the count comes out lower and the row fails, which is the
    right answer: this figure is for the full suite."""
    env = dict(os.environ, PYTHONPATH=ROOT, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only",
         "-p", "no:cacheprovider"],
        cwd=ROOT, env=env, capture_output=True, text=True)
    match = re.search(r"^(\d+) tests? collected", result.stdout, re.M)
    if not match:
        return [("reproduce:tests", "UNDERIVABLE: pytest --collect-only "
                 "printed no count")]
    return [("reproduce:tests", ".venv/bin/python -m pytest -q # %s tests"
             % match.group(1))]


def emit():
    lines = []
    for tag, level, dpi, wrong, n, rate, ladder in rows_degradation():
        lines.append((tag, "| %s | %g | %d/%d | %s | %s |"
                      % (level, dpi, wrong, n, rate, ladder)))
    for tag, label, caught, of, fr, of_correct in rows_rung_at_108():
        # The catalog row carries A percentage AND the others do NOT, which
        # is a difference in the FIGURE rather than in the markup, so it is
        # derived rather than normalized away.
        if label.startswith("7"):
            lines.append((tag, "| %s | %d / %d (%d%%) | %d / %d |"
                          % (label, caught, of, pct(caught / of), fr,
                             of_correct)))
        else:
            lines.append((tag, "| %s | %d / %d | %d / %d |"
                          % (label, caught, of, fr, of_correct)))
    for (tag, dpi, exact, n, err, lo, hi, vc, structural, caught, quiet,
         fr, of_correct) in rows_dpi_sweep():
        # A structural error is counted separately in this column, because the
        # column is about the silent family and a structural error is loud. The
        # row says so instead of folding it in.
        vc_cell = ("%d (+%d structural)" % (vc, structural)) if structural \
            else str(vc)
        lines.append((tag, "| %d | %d/%d | %d%% [%d%%, %d%%] | %s | %d | %d "
                      "| %d/%d |" % (dpi, exact, n, err, lo, hi, vc_cell,
                                     caught, quiet, fr, of_correct)))
    lines += prose_figures()
    lines += injection_headline()
    lines += ledger_rows()
    lines += investigation_scope()
    lines += collected_test_count()
    return lines


def squash(text):
    """Whitespace normalized, and blockquote markers removed with it.

    The headline sits inside a `>` blockquote whose continuation lines each
    begin with `> `. Left in, they land in the middle of a sentence after
    normalization and no derived string can ever match.
    """
    text = re.sub(r"(?m)^\s*>\s?", " ", text)
    # Emphasis is normalized away, AND THAT IS A DECISION RATHER THAN A
    # SHORTCUT. Which cell is bold is a presentation choice about where a
    # reader's eye should land: the moderate row's rate is bold and the heavy
    # row's is not, for a reason no evidence file knows. A deriver that
    # demanded the markup would have to hardcode that choice per row, and
    # would then be certifying the author's typography instead of the figures.
    # This script checks NUMBERS. Emphasis drift is a real but different
    # concern and is not pretended to be covered.
    text = text.replace("**", "")
    return re.sub(r"\s+", " ", text)


def main():
    derived = emit()
    if "--emit" in sys.argv:
        for tag, row in derived:
            print("%s\n%s" % (tag, row))
        return 0
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        readme = squash(fh.read())
    missing = [(t, r) for t, r in derived if squash(r) not in readme]
    for tag, row in missing:
        print("MISSING [%s]\n  %s" % (tag, row))
    tables = sum(1 for t, _ in derived if not t.startswith("prose:"))
    print("\n%d of %d derived figures found verbatim in README.md "
          "(%d table rows, %d in prose)"
          % (len(derived) - len(missing), len(derived), tables,
             len(derived) - tables))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
