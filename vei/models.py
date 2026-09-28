"""Model adapters.

Three of them:

  StubModel   deterministic, free, no network. It does not imitate a VLM's
              judgment; it replays a corruption chosen by the caller. Its
              only job is to exercise every path through the ladder and the
              grader so the harness is proven before a paid call is made.
              Numbers produced with the stub measure THE LADDER, not a model,
              and every artifact it writes is stamped as such.

  ClaudeModel a real vision call to Anthropic. Imported lazily so the offline
              half never needs the anthropic package installed.

  OpenAIModel the same contract against OpenAI, so a cross-provider run
              differs only in the provider. Also lazily imported.

The stub is not a simulation of a model. It is a fixture. If a result from
this repository does not say which adapter produced it, that is a bug in the
reporting, not a detail.
"""

from __future__ import annotations

import base64
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path

from .docs import Document
from .errors import Corruption, clean, corrupt
from .ladder import GROUNDED_FIELDS

STUB = "stub"
CLAUDE = "claude"
OPENAI = "openai"

# Where the stub puts a citation for a field it got wrong. See
# StubModel._evidence. This is an assumption about model behavior, not a
# measurement, and rung 5's offline catch rate is a function of it.
FAR = "far"            # 120 px clear of the true box: generous to rung 5
ADJACENT = "adjacent"  # the nearest other printed value: a one-line slip
CITATION_MISS = (FAR, ADJACENT)


def _nearest_region(doc: Document, field: str):
    """The printed value closest to `field`'s region on the page.

    This is the box a model would cite if it read the line above or below:
    on this document 6 px away for the totals block and 26 px for the PO
    number. Ties are broken by name so the stub stays deterministic.
    """
    mine = doc.regions.get(field)
    if mine is None:
        return None
    def gap(a, b) -> int:
        dx = max(0, max(b[0] - a[2], a[0] - b[2]))
        dy = max(0, max(b[1] - a[3], a[1] - b[3]))
        return max(dx, dy)
    others = [(gap(mine, r), k, r) for k, r in doc.regions.items()
              if k != field]
    return min(others)[2] if others else None


@dataclass
class Extraction:
    """What an adapter returns: the payload plus whatever evidence the
    grounded rungs need."""

    payload: str
    evidence: dict
    adapter: str
    error_class: str | None = None
    usage: dict | None = None


class StubModel:
    """Replays a chosen corruption, with citations consistent with it."""

    adapter = STUB

    # Probability that a targeted second read of a cell returns the true value
    # after the first read got it wrong.
    #
    # This number is an assumption, not a measurement. It is the guess the
    # offline half rests on most heavily: set it to 1.0 and rung 6 looks
    # perfect, set it to 0.0 and rung 6 looks useless, and neither would be a
    # finding about anything. 0.7 is a placeholder chosen only so the rung is
    # exercised in both directions. Every offline artifact records it, and the
    # real run replaces it with an observed rate. Do not quote an offline rung
    # 6 catch rate as a property of any model.
    REREAD_AGREEMENT = 0.7

    def __init__(self, seed: int = 0,
                 reread_agreement: float | None = None,
                 citation_miss: str = FAR) -> None:
        if citation_miss not in CITATION_MISS:
            raise ValueError(f"unknown citation_miss {citation_miss!r}")
        self.rng = random.Random(f"vei-stub-{seed}")
        self.reread_agreement = (self.REREAD_AGREEMENT
                                 if reread_agreement is None
                                 else reread_agreement)
        self.citation_miss = citation_miss

    def extract(self, doc: Document, *, error_class: str | None = None,
                honest_citations: bool = True) -> Extraction:
        c: Corruption = (clean(doc) if error_class is None
                         else corrupt(doc, error_class, self.rng))
        ev = self._evidence(doc, c, honest_citations)
        ev["reread"] = self._reread(doc, c)
        return Extraction(
            payload=c.payload,
            evidence=ev,
            adapter=STUB,
            error_class=c.error_class,
        )

    def _reread(self, doc: Document, c: Corruption) -> dict:
        """Second-pass values for the cells a targeted re-read would cover.

        Covers the grounded header fields, the vendor, and every line item's
        unit price and amount, that is, the cells a confusable misread lands
        in. A correct first read is confirmed; an incorrect one is corrected
        only `reread_agreement` of the time, because a model that misread a
        smudged digit once will often misread it the same way again.
        """
        truth = doc.truth()
        out: dict = {}
        for f in list(GROUNDED_FIELDS) + ["vendor"]:
            out[f] = truth[f]
        for i, item in enumerate(truth["line_items"]):
            out[f"line_items[{i}].unit_price_cents"] = item["unit_price_cents"]
            out[f"line_items[{i}].amount_cents"] = item["amount_cents"]

        corrupted = set(c.corrupted_fields)
        for f in list(out):
            if f in corrupted and self.rng.random() > self.reread_agreement:
                del out[f]      # second read repeated the first read's mistake
        return out

    def _evidence(self, doc: Document, c: Corruption,
                  honest: bool) -> dict:
        """Citations a model would return alongside the payload.

        `row_count` is the number of table rows present on the page. In this
        harness it comes from the renderer's ground truth. In a production
        system it would come from an independent layout signal (a detector, or
        a separate cheap pass) NOT from the same extraction being graded,
        which would make the check circular. That assumption is what rung 5
        rests on and is stated in the README.

        Where a misread cites is an assumption, the second most load-bearing
        guess in the offline half after REREAD_AGREEMENT. `far` offsets by 120
        px, which is well clear of everything and is generous to rung 5: a
        citation that far out misses under any tolerance anyone would
        consider. `adjacent` cites the nearest other printed value instead,
        the row above or below, 6 to 26 px away on this page, which is what a
        slip of one line actually looks like. The default is unchanged so that
        published offline numbers still mean what they said.
        """
        cites = {}
        for f in GROUNDED_FIELDS:
            region = doc.regions.get(f)
            if region is None:
                continue
            if honest or f not in c.corrupted_fields:
                cites[f] = list(region)
            elif self.citation_miss == ADJACENT:
                cites[f] = list(_nearest_region(doc, f) or region)
            else:
                # A model that misread a field tends to cite where it looked,
                # not where the value was. Offset well clear of the true box.
                x0, y0, x1, y1 = region
                cites[f] = [x0, y0 + 120, x1, y1 + 120]
        return {"citations": cites, "row_count": len(doc.items)}


class ClaudeModel:
    """A real vision extraction against the Claude API.

    The credential is read from the file ENV_FILE points at; this module never
    reads a key from anywhere else and never logs one.
    """

    adapter = CLAUDE

    def __init__(self, model: str = "claude-opus-5", max_tokens: int = 2048,
                 effort: str = "medium") -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import anthropic  # imported here so the offline half never needs it
            self._client = anthropic.Anthropic(api_key=_api_key())
        return self._client

    def extract(self, doc: Document, image_png: bytes, *,
                prompt: str) -> Extraction:
        client = self._client_lazy()
        b64 = base64.standard_b64encode(image_png).decode("ascii")
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            output_config={"effort": self.effort},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png",
                        "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        payload, evidence = _split_payload(text)
        return Extraction(
            payload=payload,
            evidence=evidence,
            adapter=CLAUDE,
            usage={
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "model": resp.model,
                "stop_reason": resp.stop_reason,
            },
        )


class OpenAIModel:
    """A real vision extraction against the OpenAI API.

    Deliberately the SAME contract as ClaudeModel: same prompt, same image
    bytes, same _split_payload, same Extraction. A cross-provider comparison is
    only meaningful if the only thing that differs is the provider, and the
    easiest way to get that wrong is to let each adapter tidy the reply its own
    way. Neither adapter is allowed to repair a malformed payload; rung 1
    exists to count those.

    The comparison is cost per Document, not per token. The two providers
    tokenize text differently and count visual tokens differently, so the same
    page is a different number of tokens on each. Price per token is therefore
    not price per page, and the only sound cross-provider comparison is cost
    per document measured on one corpus.
    """

    adapter = OPENAI

    def __init__(self, model: str = "gpt-5.6-sol", max_tokens: int = 2048,
                 effort: str = "medium") -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = None

    def _client_lazy(self):
        if self._client is None:
            import openai  # imported here so the offline half never needs it
            self._client = openai.OpenAI(api_key=_api_key("OPENAI_API_KEY"))
        return self._client

    def extract(self, doc: Document, image_png: bytes, *,
                prompt: str) -> Extraction:
        client = self._client_lazy()
        b64 = base64.standard_b64encode(image_png).decode("ascii")
        resp = client.responses.create(
            model=self.model,
            max_output_tokens=self.max_tokens,
            reasoning={"effort": self.effort},
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_image",
                     "image_url": f"data:image/png;base64,{b64}"},
                    {"type": "input_text", "text": prompt},
                ],
            }],
        )
        text = resp.output_text
        payload, evidence = _split_payload(text)
        u = resp.usage
        return Extraction(
            payload=payload,
            evidence=evidence,
            adapter=OPENAI,
            usage={
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "model": resp.model,
                "stop_reason": getattr(resp, "status", None),
            },
        )


def _brace_span_end(text: str, at: int) -> int:
    """Index just past the brace span opening at `at`, or -1 if it never
    closes. Braces inside JSON strings do not count."""
    depth, in_str, esc = 0, False, False
    for i in range(at, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _split_payload(text: str) -> tuple[str, dict]:
    """Pull the JSON object out of a model reply.

    Each top-level "{" is tried in turn, and the first complete object is
    decoded, so fences, prose and a second object around it are dropped. A
    "{" inside a span that fails to decode is never tried. In a truncated
    reply that "{" opens a line item, and decoding it would hand rung 1 a
    fragment as the reply. When nothing decodes, rung 1 gets the text
    between the first "{" and the last "}" unchanged and rejects it; on a
    paid run it therefore measures whether the reply held a decodable
    object, not whether the whole reply was clean JSON. The raw reply is not
    kept.
    """
    decoder = json.JSONDecoder()
    at = text.find("{")
    obj = None
    while at != -1:
        try:
            candidate, _ = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            # Resume after this brace span, never inside it. A span that
            # never closes is a truncated reply, and it holds no object.
            end = _brace_span_end(text, at)
            if end == -1:
                break
            at = text.find("{", end)
            continue
        if isinstance(candidate, dict):
            obj = candidate
            break
        at = text.find("{", at + 1)
    if obj is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return text, {}
        return text[start:end + 1], {}
    evidence = {}
    if isinstance(obj, dict) and "citations" in obj:
        evidence["citations"] = obj.pop("citations")
    if isinstance(obj, dict) and "row_count" in obj:
        evidence["row_count"] = obj.pop("row_count")
    return json.dumps(obj), evidence


def _api_key(name: str = "ANTHROPIC_API_KEY") -> str:
    """Read `name` from the file ENV_FILE points at.

    Never falls back to a key already in the environment: an accidental
    ambient key is exactly how a run gets billed to the wrong account. That
    guarantee is per-provider. Adding OpenAI support did not add an
    os.environ fallback for OPENAI_API_KEY either.
    """
    env_file = os.environ.get("ENV_FILE")
    if not env_file:
        raise RuntimeError(
            "ENV_FILE is not set. Run with:\n"
            "    ENV_FILE=~/.secrets/ai.env python scripts/real_run.py --probe")
    path = Path(env_file).expanduser()
    if not path.is_file():
        raise RuntimeError(f"ENV_FILE points at {path}, which does not exist")
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == name:
            return v.strip().strip('"').strip("'")
    raise RuntimeError(f"no {name} line in {path}")
