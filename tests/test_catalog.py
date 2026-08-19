"""Rung 7 (catalog membership) and the sku_near_miss class.

Rung 7 exists because the degraded-scan run found the ladder had no check
aimed at the field that actually moved. 15 of 19 real errors at 108 DPI were
misread part numbers, and rungs 0-6 caught almost none of them.

A rung added AFTER seeing the data it is meant to catch has to be held to a
higher standard than one designed in advance, because it is trivially easy to
write a check that fits the errors you already looked at. Two things guard
against that here: it must not reject correct documents (test below), and its
real value is measured by scripts/regrade.py against payloads the model
produced BEFORE the rung existed.
"""

from __future__ import annotations

import json
import random

from vei.docs import CATALOG_SKUS, corpus, make_document
from vei.errors import FAMILIES, VALUE_CONSISTENT, clean, corrupt
from vei.ladder import (check_catalog, rung_catalog, rung_cross_field,
                        rung_strict)


def test_catalog_covers_every_sku_the_generator_emits():
    """If the parts master were incomplete, rung 7 would reject correct
    documents and its false-rejection rate would be an artifact of the
    fixture rather than a property of the check."""
    for doc in corpus(60):
        for item in doc.items:
            assert item.sku in CATALOG_SKUS, f"{item.sku} missing from catalog"


def test_catalog_check_accepts_every_clean_document():
    """The floor. A check that rejects correct input has no useful catch rate."""
    for doc in corpus(40):
        v = check_catalog(clean(doc).payload)
        assert v.accepted, f"{doc.doc_id} rejected: {v.reason}"


def test_sku_near_miss_is_a_genuine_consistent_value_error():
    """It must survive EVERY rung below 7. If any of them catches it, rung 7 is
    not the thing doing the work and its measured value is borrowed."""
    assert FAMILIES["sku_near_miss"] == VALUE_CONSISTENT
    rng = random.Random("sku")
    hits = 0
    for doc in corpus(40):
        c = corrupt(doc, "sku_near_miss", rng)
        if c.error_class is None:
            continue
        hits += 1
        # Rungs 5 and 6 need citations, which this call deliberately does not
        # supply; they are exercised with real evidence by scripts/regrade.py.
        for rung in (rung_strict, rung_cross_field):
            v = rung(c.payload, doc, None)
            assert v.accepted, (
                f"sku_near_miss was caught by {v.rung} ({v.reason}); it is not "
                f"a consistent-value error and rung 7 is not what catches it")
    assert hits >= 30, f"only {hits} documents produced a sku_near_miss"


def test_catalog_check_catches_sku_near_miss():
    rng = random.Random("sku2")
    caught = seen = 0
    for doc in corpus(40):
        c = corrupt(doc, "sku_near_miss", rng)
        if c.error_class is None:
            continue
        seen += 1
        if not check_catalog(c.payload).accepted:
            caught += 1
    assert seen and caught == seen, f"rung 7 caught only {caught}/{seen}"


def test_sku_near_miss_changes_exactly_one_character():
    """A corruption that mangles the whole field would be caught by anything.
    The claim is that a ONE-CHARACTER misread is invisible to rungs 1-6."""
    rng = random.Random("sku3")
    for doc in corpus(20):
        c = corrupt(doc, "sku_near_miss", rng)
        if c.error_class is None:
            continue
        got = json.loads(c.payload)["line_items"]
        truth = doc.truth()["line_items"]
        diffs = [(g["sku"], t["sku"]) for g, t in zip(got, truth)
                 if g["sku"] != t["sku"]]
        assert len(diffs) == 1, f"expected one changed SKU, got {diffs}"
        a, b = diffs[0]
        assert len(a) == len(b), f"length changed: {a} vs {b}"
        assert sum(x != y for x, y in zip(a, b)) == 1, f"{a} vs {b}"


def test_catalog_rung_is_cumulative():
    """Rung 7 must still reject everything rung 6 rejects. If it did not, the
    ladder would stop being monotonic and a flat segment in the results would
    no longer be a finding."""
    rng = random.Random("cum")
    doc = make_document(0)
    for cls in ("malformed_json", "missing_required", "total_broken",
                "date_order_swapped"):
        c = corrupt(doc, cls, rng)
        assert not rung_catalog(c.payload, doc, None).accepted, (
            f"rung 7 accepted {cls}, which a lower rung rejects")


def test_catalog_check_needs_no_page_and_no_second_call():
    """The reason rung 7's number is not its cost order: the check takes only
    a payload, so it is cheaper than rungs 5 and 6 despite sitting above them."""
    doc = make_document(3)
    assert not doc.regions, "this test needs an unrendered document"
    rng = random.Random("cheap")
    c = corrupt(doc, "sku_near_miss", rng)
    assert not check_catalog(c.payload).accepted
    assert check_catalog(clean(doc).payload).accepted
