"""scripts/regrade.py grades rungs 5 to 7 only on pages it can rebuild.

It rebuilds a clean page at the default resolution, or a degraded one for a run
keyed by level. Graded against that page, an injected page's citations, a
downscaled page's, or a record with no stored evidence all "miss", and the tool
reported correct extractions as confident rejections.
"""

import sys

import pytest

import scripts.regrade as regrade

pytest.importorskip("PIL")


def test_pages_it_cannot_rebuild_are_named():
    ev = {"citations": {}}
    assert regrade.page_unknown({"evidence": ev}) == ""
    assert regrade.page_unknown({"evidence": ev, "level": "moderate"}) == ""
    assert regrade.page_unknown({"evidence": ev, "arm": "none"}) == ""
    assert regrade.page_unknown({}) == "no evidence recorded"
    assert regrade.page_unknown({"evidence": ev, "dpi": 90}) != ""
    assert regrade.page_unknown({"evidence": ev, "arm": "metadata_field"}) == "injected page"


def test_an_injected_run_is_not_reported_as_rejecting_correct_extractions(
        monkeypatch, capsys):
    """The opus fixed-page run: 0 of 40 false rejections on the control arm,
    live. Graded against a clean page, its injected arm used to add 11."""
    monkeypatch.setattr(sys, "argv", [
        "regrade.py", "audit/inject_consistent_n40_opus5_fixedpage.json"])
    assert regrade.main() == 0
    out = capsys.readouterr().out
    assert "NOT EXAMINED for injected page (40)" in out
    grounded = next(l for l in out.splitlines() if "5_grounded" in l)
    assert grounded.rstrip().endswith("false rejections 0/40"), grounded
