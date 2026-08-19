"""Re-derive every published number in README.md from audit/*.json and diff them.

A README is prose and drifts; audit/*.json is evidence and does not. This
script rebuilds each figure from the JSON and asserts the exact string is
present in the README, so a re-run that shifts a figure fails loudly instead of
leaving the document quietly wrong.

    python3 scripts/check_readme_numbers.py            check
    python3 scripts/check_readme_numbers.py --emit     print what it derives

Whitespace is normalized on both sides. Several of the figures here sit inside
sentences that wrap across three lines, and a deriver that demanded a literal
match would fire on every reflow until a reader learned to ignore it.

What it does NOT cover, said here rather than left to be discovered. The
offline ladder tables, the IoU medians and the citation-honesty family grid
come from the constructed corpus rather than from a shipped run, and the
prompt A/B figures come from a run this file does not read. THE COUNT IS
Printed whether OR NOT anything is missing, so a version of this script that
quietly stopped deriving half of them is visible rather than clean.

A figure whose definition could NOT be pinned exactly is left out.
A deriver that encodes a guess at what a number meant is worse than no deriver:
it passes, and it certifies the guess.
"""

import glob
import json
import math
import os
import re
import sys

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
    """What each rung caught at the one quality where the errors are quiet."""
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
    """Where the quiet errors appear as the page gets worse."""
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
    # The three quiet models share A denominator OR the sentence is wrong.
    # "0 of 40 from three of the four" only reads correctly while all three
    # have the same clean-arm size; if a re-run changes one, this derivation
    # fails rather than quietly agreeing with a sentence that no longer holds.
    if len(quiet) == 3 and len(denominators) == 1:
        out.append(("prose:clean-arm",
                    "rejected %d of %d correct extractions from three of the "
                    "four models and %d of %d from `claude-sonnet-5`,"
                    % (0, denominators.pop(),
                       s["clean_rejected"], s["clean"])))
    else:
        out.append(("prose:clean-arm", "UNDERIVABLE: the three quiet models no "
                    "longer share one denominator"))
    return out


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
        # column is about the QUIET family and a structural error is loud. The
        # row says so instead of folding it in.
        vc_cell = ("%d (+%d structural)" % (vc, structural)) if structural \
            else str(vc)
        lines.append((tag, "| %d | %d/%d | %d%% [%d%%, %d%%] | %s | %d | %d "
                      "| %d/%d |" % (dpi, exact, n, err, lo, hi, vc_cell,
                                     caught, quiet, fr, of_correct)))
    lines += prose_figures()
    return lines


def squash(text):
    """Whitespace normalized, and blockquote markers removed with it.

    The headline sits inside a `>` blockquote whose continuation lines each
    begin with `> `. Left in, they land in the middle of a sentence after
    normalization and no derived string can ever match.
    """
    text = re.sub(r"(?m)^\s*>\s?", " ", text)
    text = text.replace(" > ", " ")
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
