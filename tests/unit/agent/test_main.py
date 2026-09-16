"""Tests for `agent.main.agent_invocation` (design.md §5; `agent-runtime`
spec, *Stateless Single-Turn Answers*).

No network: `build_agent` is always monkeypatched to a fake factory
returning a fake callable agent, never the real Strands `Agent`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import agent.main as main


class _FakeAgent:
    """Stands in for a Strands `Agent`: `str(agent(prompt))` returns fixed text."""

    def __init__(self, raw_text: str) -> None:
        self._raw_text = raw_text
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._raw_text


def _json_agent(answer: str, language: str) -> _FakeAgent:
    return _FakeAgent(json.dumps({"answer": answer, "language": language}))


def test_fresh_agent_built_per_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """design.md D5 — the single most important line of the agent: a fresh
    `Agent` per invocation, never module-scope."""
    built: list[_FakeAgent] = []

    def _factory() -> _FakeAgent:
        fresh = _json_agent("hi", "en")
        built.append(fresh)
        return fresh

    monkeypatch.setattr(main, "build_agent", _factory)

    main.agent_invocation({"prompt": "hello"})
    main.agent_invocation({"prompt": "hello again"})

    assert len(built) == 2
    assert built[0] is not built[1]
    assert built[0].calls == ["hello"]
    assert built[1].calls == ["hello again"]


def test_unhandled_exception_never_returns_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raising_factory() -> Any:
        raise RuntimeError("boom: internal detail that must never leak")

    monkeypatch.setattr(main, "build_agent", _raising_factory)

    result = main.agent_invocation({"prompt": "hello"})

    assert "boom" not in result["answer"]
    assert "Traceback" not in result["answer"]
    assert result["language"] == "en"


def test_returns_answer_and_language_parsed_from_model_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "build_agent", lambda: _json_agent("I built a chatbot.", "es"))

    result = main.agent_invocation({"prompt": "que hiciste?"})

    assert result == {"answer": "I built a chatbot.", "language": "es"}


def test_missing_prompt_returns_generic_response_without_building_an_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(main, "build_agent", lambda: calls.append(1) or _json_agent("x", "en"))

    result = main.agent_invocation({})

    assert calls == []
    assert result["language"] == "en"
    assert result["answer"]


@pytest.mark.parametrize("bad_prompt", ["", "   ", 123, None])
def test_non_string_or_blank_prompt_returns_generic_response(
    bad_prompt: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(main, "build_agent", lambda: calls.append(1) or _json_agent("x", "en"))

    result = main.agent_invocation({"prompt": bad_prompt})

    assert calls == []
    assert result["language"] == "en"


def test_non_json_model_output_falls_back_to_raw_text_as_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "build_agent", lambda: _FakeAgent("I worked on many things."))

    result = main.agent_invocation({"prompt": "tell me about your work"})

    assert result["answer"] == "I worked on many things."
    assert result["language"] == "en"


def test_json_output_missing_answer_key_falls_back_to_raw_text(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = json.dumps({"language": "en"})
    monkeypatch.setattr(main, "build_agent", lambda: _FakeAgent(raw))

    result = main.agent_invocation({"prompt": "hello"})

    assert result["answer"] == raw
    assert result["language"] == "en"


@pytest.mark.parametrize("bad_answer", ["", "   ", 42, None])
def test_json_output_with_blank_or_non_string_answer_falls_back_to_raw_text(
    bad_answer: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.dumps({"answer": bad_answer, "language": "es"})
    monkeypatch.setattr(main, "build_agent", lambda: _FakeAgent(raw))

    result = main.agent_invocation({"prompt": "hello"})

    assert result["answer"] == raw
    assert result["language"] == "en"


def test_long_answer_is_hard_capped_to_max_answer_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    """MAJOR/MEDIUM finding: bound the answer length regardless of model
    output size, defaulting to `MAX_ANSWER_CHARS=1200` (`agent.settings`)."""
    long_answer = "This is one sentence. " * 200  # far past the 1200-char default
    monkeypatch.setattr(main, "build_agent", lambda: _json_agent(long_answer, "en"))

    result = main.agent_invocation({"prompt": "tell me everything"})

    assert len(result["answer"]) <= 1200


def test_answer_truncation_prefers_a_sentence_boundary() -> None:
    text = "First sentence. Second sentence. " + "x" * 2000
    truncated = main._truncate_answer(text, max_chars=33)

    assert truncated == "First sentence. Second sentence."


def test_answer_truncation_falls_back_to_a_word_boundary_without_sentence_punctuation() -> None:
    text = "word " * 10  # no sentence punctuation at all
    truncated = main._truncate_answer(text, max_chars=12)

    assert truncated == "word word"
    assert not truncated.endswith(" ")


def test_answer_truncation_hard_cuts_a_single_long_word() -> None:
    text = "x" * 50
    truncated = main._truncate_answer(text, max_chars=10)

    assert truncated == "x" * 10


def test_answer_within_the_limit_is_returned_unchanged() -> None:
    text = "short answer."
    assert main._truncate_answer(text, max_chars=1200) == text


def test_overly_long_prompt_is_rejected_before_building_an_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """LOW finding (defense in depth): a 1001+ char prompt is rejected with
    the generic fallback response before `build_agent`/the agent are ever
    invoked, independent of any model-side length limit."""
    calls = []
    monkeypatch.setattr(main, "build_agent", lambda: calls.append(1) or _json_agent("x", "en"))

    result = main.agent_invocation({"prompt": "x" * 1001})

    assert calls == []
    assert result["language"] == "en"
    assert result["answer"]


def test_prompt_at_exactly_1000_chars_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "build_agent", lambda: _json_agent("hi", "en"))

    result = main.agent_invocation({"prompt": "x" * 1000})

    assert result == {"answer": "hi", "language": "en"}


def test_bogus_language_hint_is_ignored_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """`language_hint` is accepted in the payload and intentionally unused in
    v1 (`design.md` §6) — the agent always detects the language itself, so a
    bogus value must never cause an error or change behavior."""
    monkeypatch.setattr(main, "build_agent", lambda: _json_agent("I built a chatbot.", "es"))

    result = main.agent_invocation({"prompt": "que hiciste?", "language_hint": "not-a-real-language"})

    assert result == {"answer": "I built a chatbot.", "language": "es"}
