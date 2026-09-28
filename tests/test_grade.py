"""vei/grade.py: the arithmetic every published figure passes through.

Why this file exists. Nothing imported this module. `wilson` produces every
confidence interval in the README and could be short-circuited to (0.0, 1.0)
with the whole suite green; the clean-document branch of `record` is the ONLY
mechanism enforcing the module's own stated invariant, "A gate that rejects
everything has a perfect catch rate and is worthless", and it could be zeroed
the same way.

The figures were correct. Nothing held them there.
"""

from __future__ import annotations

import pytest

from vei.grade import Results, wilson


# ------------------------------------------------------- the interval

def test_wilson_brackets_the_point_estimate():
    for caught, seen in ((0, 40), (1, 40), (20, 40), (39, 40), (40, 40)):
        lo, hi = wilson(caught, seen)
        p = caught / seen
        assert 0.0 <= lo <= p <= hi <= 1.0, f"{caught}/{seen} -> ({lo}, {hi})"


def test_wilson_has_width_at_the_boundaries():
    """The reason this function exists rather than the normal approximation.
    Several published cells sit at 0/n or n/n, where the normal interval has
    zero width and is simply wrong. An interval of zero width at a boundary is
    the failure mode, so it is asserted."""
    for caught, seen in ((0, 90), (40, 40), (0, 40), (25, 25)):
        lo, hi = wilson(caught, seen)
        assert hi - lo > 0.01, f"{caught}/{seen} gave a degenerate interval"


def test_wilson_narrows_as_the_sample_grows():
    """STRICTLY narrower, not only non-increasing. A function returning a
    constant interval satisfies non-increasing and measures nothing."""
    widths = [wilson(n // 2, n)[1] - wilson(n // 2, n)[0] for n in (10, 40, 200, 1000)]
    for a, b in zip(widths, widths[1:]):
        assert b < a, f"the interval did not narrow: {widths}"


def test_wilson_reproduces_published_intervals():
    """Four cells taken from the README, to the precision it prints them."""
    for caught, seen, want_lo, want_hi in (
            (19, 25, 56.6, 88.5),
            (25, 25, 86.7, 100.0),
            (0, 90, 0.0, 4.1),
            (34, 40, 70.9, 92.9)):
        lo, hi = wilson(caught, seen)
        assert round(lo * 100, 1) == want_lo, f"{caught}/{seen} low"
        assert round(hi * 100, 1) == want_hi, f"{caught}/{seen} high"


def test_no_sample_is_the_widest_interval_and_not_a_measurement():
    assert wilson(0, 0) == (0.0, 1.0)


# ------------------------------------------- the false-rejection column

def test_a_rung_that_rejects_a_clean_document_is_counted_as_wrong():
    """The module's load-bearing invariant, in its own words: a gate that
    rejects everything has a perfect catch rate and is worthless. That is only
    visible because clean documents are recorded separately and a rejection
    there counts AGAINST the rung. If this branch stops counting, a
    reject-everything rung prints a perfect score and a spotless false
    rejection rate side by side."""
    r = Results()
    for _ in range(10):
        r.record("5_grounded", None, rejected=True)
    cell = r.to_dict()["clean"]["5_grounded"]
    assert cell["seen"] == 10
    assert cell["false_rejections"] == 10
    assert cell["false_rejection_rate"] == 1.0


def test_a_rung_that_accepts_every_clean_document_scores_zero_there():
    r = Results()
    for _ in range(10):
        r.record("5_grounded", None, rejected=False)
    cell = r.to_dict()["clean"]["5_grounded"]
    assert cell["seen"] == 10 and cell["false_rejections"] == 0
    assert cell["false_rejection_rate"] == 0.0


def test_a_reject_everything_rung_is_distinguishable_from_a_perfect_one():
    """The two rungs below have IDENTICAL catch rates. Only the clean column
    separates them, which is the whole argument for reporting the pair."""
    r = Results()
    for _ in range(20):
        r.record("perfect", "misread_digit_reconciled", rejected=True)
        r.record("perfect", None, rejected=False)
        r.record("paranoid", "misread_digit_reconciled", rejected=True)
        r.record("paranoid", None, rejected=True)
    d = r.to_dict()
    assert (d["by_class"]["perfect"]["misread_digit_reconciled"]["rate"]
            == d["by_class"]["paranoid"]["misread_digit_reconciled"]["rate"] == 1.0)
    assert d["clean"]["perfect"]["false_rejection_rate"] == 0.0
    assert d["clean"]["paranoid"]["false_rejection_rate"] == 1.0


def test_clean_and_corrupted_records_do_not_land_in_the_same_cell():
    r = Results()
    r.record("5_grounded", None, rejected=True)
    r.record("5_grounded", "misread_digit_reconciled", rejected=True)
    d = r.to_dict()
    assert d["clean"]["5_grounded"]["seen"] == 1
    assert d["by_class"]["5_grounded"]["misread_digit_reconciled"]["seen"] == 1
