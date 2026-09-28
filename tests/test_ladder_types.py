"""Rungs 2, 3 and 5 reject a mistyped reply instead of raising on it.

A model can put any JSON value in any slot. The README describes rung 2 as
"required fields, correct primitive types" and rung 3 as a strict schema; a
reply those rungs let through was then compared as a date or indexed as a box
further up the ladder, and the exception ended a paid run with its records
unwritten.
"""

import json

import pytest

from vei.docs import make_document
from vei.errors import clean
from vei.ladder import rung_grounded, rung_strict, rung_typed


def _payload(**changes):
    doc = make_document(0)
    obj = json.loads(clean(doc).payload)
    obj.update(changes)
    return doc, json.dumps(obj)


def test_the_clean_payload_passes_the_shape_rungs():
    doc, payload = _payload()
    assert rung_strict(payload, doc, None).accepted


@pytest.mark.parametrize("field, value", [
    ("po_number", 123), ("vendor", None), ("order_date", 20260801),
    ("due_date", ["2026-09-01"]),
])
def test_rung_2_rejects_a_mistyped_required_field(field, value):
    doc, payload = _payload(**{field: value})
    verdict = rung_typed(payload, doc, None)
    assert not verdict.accepted and field in verdict.reason


def test_rung_3_rejects_a_date_that_is_not_iso():
    doc, payload = _payload(order_date="not a date")
    verdict = rung_strict(payload, doc, None)
    assert not verdict.accepted and "YYYY-MM-DD" in verdict.reason


@pytest.mark.parametrize("box", [[1, 2, 3], None, "10,10,20,20"])
def test_rung_5_rejects_a_malformed_citation_instead_of_raising(box):
    doc, payload = _payload()
    cites = {"po_number": box, "total_cents": [0, 0, 1, 1],
             "subtotal_cents": [0, 0, 1, 1]}
    verdict = rung_grounded(payload, doc, {"citations": cites})
    assert not verdict.accepted and "not a box" in verdict.reason


def test_rung_5_rejects_citations_that_are_not_a_mapping():
    doc, payload = _payload()
    verdict = rung_grounded(payload, doc, {"citations": [[0, 0, 1, 1]]})
    assert not verdict.accepted and "mapping" in verdict.reason
