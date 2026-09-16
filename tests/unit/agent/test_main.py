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
