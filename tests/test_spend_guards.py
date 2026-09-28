"""The guards SECURITY.md describes, each asserted rather than described.

Every paid script is a dry run until --confirm, refuses an estimate above
--max-cost, and reads its key only from the file ENV_FILE names. None of these
tests sends a request: each replaces the model adapter with one that fails the
test if it is ever constructed.
"""

import sys

import pytest

import scripts.inject_run as inject_run
import scripts.real_run as real_run
from vei import models
from vei.render import can_render

# Two guards sit after the page is drawn, so reaching them needs Pillow and a
# font. They skip where those are missing; the other tests here run anywhere.
needs_rendering = pytest.mark.skipif(
    not can_render(),
    reason="the guard sits after a page render; the rendering CI job covers it")


class _MustNotRun:
    def __init__(self, *args, **kwargs):
        raise AssertionError("a model adapter was built, so a call could be billed")


def test_an_ambient_key_is_never_used(monkeypatch):
    monkeypatch.delenv("ENV_FILE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient-must-not-be-read")
    with pytest.raises(RuntimeError, match="ENV_FILE is not set"):
        models._api_key("ANTHROPIC_API_KEY")


def test_the_env_file_is_the_only_source_of_the_key(monkeypatch, tmp_path):
    env_file = tmp_path / "ai.env"
    env_file.write_text("OTHER=1\n")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient-must-not-be-read")
    with pytest.raises(RuntimeError, match="no ANTHROPIC_API_KEY line"):
        models._api_key("ANTHROPIC_API_KEY")


@needs_rendering
def test_without_confirm_nothing_is_built_or_sent(monkeypatch, tmp_path):
    monkeypatch.setattr(real_run, "ClaudeModel", _MustNotRun)
    monkeypatch.setattr(sys, "argv", ["real_run.py", "--n", "1",
                                      "--out", str(tmp_path / "run.json")])
    assert real_run.main() == 0
    assert not (tmp_path / "run.json").exists()


@needs_rendering
def test_an_estimate_over_max_cost_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(inject_run, "ClaudeModel", _MustNotRun)
    monkeypatch.setattr(inject_run, "OpenAIModel", _MustNotRun)
    monkeypatch.setattr(sys, "argv", ["inject_run.py", "--n", "1",
                                      "--max-cost", "0", "--confirm",
                                      "--out", str(tmp_path / "run.json")])
    assert inject_run.main() == 2
    assert not (tmp_path / "run.json").exists()


@pytest.mark.parametrize("module", ["real_run", "degrade_run", "dpi_sweep"])
def test_a_claude_only_script_refuses_an_openai_model(module, monkeypatch):
    """These scripts always build ClaudeModel. Accepting an OpenAI id would
    price the run at OpenAI rates, send it to the Anthropic API, and stamp
    the artifact with the wrong adapter."""
    import importlib
    mod = importlib.import_module(f"scripts.{module}")
    monkeypatch.setattr(sys, "argv", [f"{module}.py", "--model", "gpt-5.4"])
    with pytest.raises(SystemExit) as refused:
        mod.main()
    assert refused.value.code == 2, "argparse rejects an invalid choice with 2"


@pytest.mark.parametrize("module", ["real_run", "inject_run", "degrade_run",
                                    "dpi_sweep"])
def test_a_paid_run_will_not_write_over_an_existing_artifact(module, tmp_path,
                                                             monkeypatch):
    """The default --out of each paid script is an artifact the repository
    ships. With --confirm and an existing file it refuses before any adapter
    is built, so nothing is spent and nothing is replaced."""
    import importlib
    mod = importlib.import_module(f"scripts.{module}")
    for name in ("ClaudeModel", "OpenAIModel"):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, _MustNotRun)
    existing = tmp_path / "published.json"
    existing.write_text("{}")
    # A ceiling no estimate reaches, so the refusal can only be this guard's.
    monkeypatch.setattr(sys, "argv", [f"{module}.py", "--confirm",
                                      "--max-cost", "100000",
                                      "--out", str(existing)])
    assert mod.main() == 2
    assert existing.read_text() == "{}"


def test_the_first_complete_object_is_read_when_a_reply_carries_two():
    """A reply with two objects, or a braced word in prose before the object,
    lost its citations to a span that would not parse."""
    import json

    payload, evidence = models._split_payload(
        'Here {is} the result: {"po_number": "PO-1", "citations": {"x": [1, 2, 3, 4]}}\n'
        '{"po_number": "PO-2"}')
    assert json.loads(payload) == {"po_number": "PO-1"}
    assert evidence == {"citations": {"x": [1, 2, 3, 4]}}
    assert models._split_payload("no object here {") == ("no object here {", {})


def test_a_truncated_reply_yields_no_object_not_its_first_line_item():
    """A reply cut off mid-object fails to decode at its own "{". The "{" of
    its first line item is nested inside that span, and decoding it would
    hand rung 1 a line item as if it were the reply."""
    import json

    truncated = ('{"po_number": "PO-1", "vendor": "X", "line_items": '
                 '[{"row": 1, "sku": "A", "amount_cents": 5}, '
                 '{"row": 2, "sku": "B"')
    payload, evidence = models._split_payload(truncated)
    assert evidence == {}
    assert payload.startswith('{"po_number"')
    with pytest.raises(json.JSONDecodeError):
        json.loads(payload)
    # A braced word in prose is a closed span, so the object after it is read.
    payload, _ = models._split_payload('See {note}: {"po_number": "PO-2"}')
    assert json.loads(payload) == {"po_number": "PO-2"}
