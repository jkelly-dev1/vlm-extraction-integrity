"""The two prompts in the citation experiment must differ by ONE thing.

A prompt comparison is only a measurement of the added sentence if everything
else about the two prompts is identical. Nothing enforces that by
construction. Both are module-level strings, and a later edit to the base
prompt that is not mirrored into the variant would leave two arms differing in
ways nobody recorded, still producing a clean-looking table.
"""

from __future__ import annotations

from scripts.citation_prompt import (CITE_VALUE_CLAUSE, PROMPT_CITE_VALUE,
                                     PROMPTS, STANDARD, AMENDED)
from scripts.real_run import PROMPT


def test_the_variant_is_the_base_prompt_plus_one_clause():
    assert PROMPT_CITE_VALUE == PROMPT + CITE_VALUE_CLAUSE
    assert PROMPT_CITE_VALUE.startswith(PROMPT)
    # An append, not a rewrite: the extraction task the model is given must be
    # character-for-character the one every other run in this repo used.
    assert PROMPTS[STANDARD] == PROMPT
    assert PROMPTS[AMENDED][:len(PROMPT)] == PROMPT


def test_both_prompts_ask_for_the_same_fields():
    """The variant may add instructions about HOW to cite, never about WHAT
    to extract. A drifted field list would change the payload being graded."""
    for field in ("po_number", "order_date", "due_date", "vendor", "terms",
                  "currency", "line_items", "subtotal_cents", "tax_cents",
                  "total_cents", "row_count", "citations"):
        assert PROMPTS[STANDARD].count(field) == PROMPTS[AMENDED].count(field), (
            f"{field} appears a different number of times in the two prompts")


def test_the_clause_actually_says_something():
    """Guards the degenerate version of this experiment: an empty or
    whitespace-only clause would make both arms identical and the result would
    be a measurement of run-to-run noise reported as a prompt effect."""
    assert len(CITE_VALUE_CLAUSE.split()) >= 10
    assert PROMPT_CITE_VALUE != PROMPT
