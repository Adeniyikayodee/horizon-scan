"""F4: the memo arrives whole, and its shape has one definition.

Before this the frame asked for eight to twelve pages, context/output_spec.md asked
for two to three, the model followed the shorter, truncation was undetectable on the
OpenRouter path the app hard-codes, and nothing measured the result. Median delivered
memo was about three pages.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from scan import agents, client, config, mock
from scan import spec as S

SPEC = dict(S.DEFAULT_SPEC)


# --- one definition, two consumers --------------------------------------------------
def test_headings_in_the_frame_come_from_the_spec():
    frame = agents.synth_instructions(SPEC)
    for s in S.memo_spec(SPEC)["sections"]:
        assert f"## {s['heading']}" in frame, f"{s['heading']} never reaches the model"


def test_length_shown_to_the_model_is_derived_from_the_checked_floor():
    text = S.memo_target_text(SPEC)
    assert f"{S.memo_spec(SPEC)['min_words']:,}" in text
    assert text in agents.synth_instructions(SPEC)


def test_editing_the_spec_moves_the_prompt_and_the_check_together():
    custom = dict(SPEC)
    custom["memo"] = {"min_words": 100, "words_per_page": 50,
                      "sections": [{"heading": "Only section", "guidance": "Say the thing."}]}
    frame = agents.synth_instructions(custom)
    assert "## Only section" in frame and "Executive summary" not in frame
    md = "# T\n\n## Only section\n\n" + ("word " * 200)
    assert S.memo_shortfall(md, custom) == ""


def test_output_spec_no_longer_states_a_second_length():
    text = (config.CONTEXT_DIR / "output_spec.md").read_text(encoding="utf-8").lower()
    assert "two to three pages" not in text, "the contradicting length statement is back"


# --- the shortfall check ------------------------------------------------------------
def test_short_memo_is_reported():
    md = "# Title\n\n" + "\n".join(f"## {s['heading']}\n\nA line.\n"
                                   for s in S.memo_spec(SPEC)["sections"])
    out = S.memo_shortfall(md, SPEC)
    assert "against a floor of" in out


def test_missing_section_is_reported():
    md = "# Title\n\n## Executive summary\n\n" + ("word " * 5000)
    out = S.memo_shortfall(md, SPEC)
    assert "missing section" in out and "Conclusion and recommendations" in out


def test_whole_memo_passes():
    body = "word " * (S.memo_spec(SPEC)["min_words"] // len(S.memo_spec(SPEC)["sections"]) + 60)
    md = "# Title\n\n" + "\n".join(f"## {s['heading']}\n\n{body}\n"
                                   for s in S.memo_spec(SPEC)["sections"])
    assert S.memo_shortfall(md, SPEC) == ""


# --- truncation is detectable on BOTH providers --------------------------------------
class _Fn:
    def __init__(self, args): self.arguments = args


class _TC:
    def __init__(self, args): self.function = _Fn(args)


class _Msg:
    def __init__(self, args=None, content=None):
        self.tool_calls = [_TC(args)] if args is not None else None
        self.content = content


class _Choice:
    def __init__(self, msg, finish): self.message, self.finish_reason = msg, finish


class _Resp:
    def __init__(self, msg, finish="stop"):
        self.choices = [_Choice(msg, finish)]
        self.usage = None


def _openrouter_returning(resp):
    class _Completions:
        async def create(self, **kw): return resp

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()
    return _Client()


def test_openrouter_finish_length_raises_truncated(monkeypatch):
    monkeypatch.setattr(client, "or_client",
                        lambda: _openrouter_returning(_Resp(_Msg('{"a":1}'), finish="length")))
    with pytest.raises(client.TruncatedOutput):
        asyncio.run(client._openrouter_call("f", "u", {}, False, 100, "m"))


def test_openrouter_half_written_arguments_raise_truncated(monkeypatch):
    """This used to surface as a JSONDecodeError and kill stage two."""
    monkeypatch.setattr(client, "or_client",
                        lambda: _openrouter_returning(_Resp(_Msg('{"memo_markdown": "half a sent'))))
    with pytest.raises(client.TruncatedOutput):
        asyncio.run(client._openrouter_call("f", "u", {}, False, 100, "m"))


def test_openrouter_good_response_still_parses(monkeypatch):
    monkeypatch.setattr(client, "or_client",
                        lambda: _openrouter_returning(_Resp(_Msg(json.dumps({"ok": True})))))
    assert asyncio.run(client._openrouter_call("f", "u", {}, False, 100, "m")) == {"ok": True}


# --- the retry path: one code path covers both providers ------------------------------
def test_synthesize_retries_on_truncation_then_delivers(monkeypatch):
    calls = []

    async def _call(**kw):
        calls.append(kw["max_tokens"])
        if len(calls) == 1:
            raise client.TruncatedOutput("m", kw["max_tokens"])
        body = "word " * (S.memo_spec(SPEC)["min_words"] // len(S.memo_spec(SPEC)["sections"]) + 60)
        md = "# Title\n\n" + "\n".join(f"## {s['heading']}\n\n{body}\n"
                                       for s in S.memo_spec(SPEC)["sections"])
        return {"memo_markdown": md, "scorecard_intro": "x"}

    monkeypatch.setattr(agents, "structured_call", _call)
    monkeypatch.setattr(config, "SPEC", dict(SPEC))
    out = asyncio.run(agents.synthesize({"mission": "", "output_spec": "", "exemplar": ""}, []))
    assert len(calls) == 2 and calls[1] > calls[0]
    assert S.memo_shortfall(out["memo_markdown"], SPEC) == ""


def test_synthesize_retries_on_a_short_memo(monkeypatch):
    calls = []

    async def _call(**kw):
        calls.append(kw["max_tokens"])
        return {"memo_markdown": "# T\n\n## Executive summary\n\nToo short.", "scorecard_intro": "x"}

    monkeypatch.setattr(agents, "structured_call", _call)
    monkeypatch.setattr(config, "SPEC", dict(SPEC))
    out = asyncio.run(agents.synthesize({"mission": "", "output_spec": "", "exemplar": ""}, []))
    assert len(calls) == 2, "a short memo must be retried, not silently accepted"
    assert out["memo_markdown"]                      # fullest draft still delivered


# --- the dry run models the real shape ------------------------------------------------
def test_mock_memo_satisfies_the_spec():
    config.SPEC = dict(SPEC)
    memo = mock._synth()["memo_markdown"]
    assert S.memo_shortfall(memo, SPEC) == "", "the dry run should exercise a conforming memo"
