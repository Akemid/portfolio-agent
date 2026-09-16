"""Tests for `agent.agent_factory.build_agent` (design.md §5; `agent-runtime`
spec, *Foundation Model*, *No Side-Effect Tools*).

No network: constructing a `BedrockModel` or a `boto3` client only builds a
local object — no call is made until the agent is actually invoked, which
these tests never do.
"""

from __future__ import annotations

from typing import Any

import pytest

import agent.agent_factory as agent_factory_module
from agent.agent_factory import build_agent
from agent.settings import ConfigError


class _FakeBedrockModel:
    """Captures the kwargs `agent_factory.build_agent` passes to `BedrockModel`,
    so tests can assert on output-bounding config without a live model call.

    `stateful = False` mimics the real `BedrockModel`'s attribute — the
    Strands `Agent.__init__` reads it directly (`strands/agent/agent.py`),
    so a bare fake without it raises `AttributeError` before construction
    even completes.
    """

    stateful = False

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def get_config(self) -> dict[str, Any]:
        return self.kwargs


@pytest.fixture(autouse=True)
def _agent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_ID", "amazon.nova-micro-v1:0")
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "KB0000000000")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def test_model_id_read_from_env_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_ID", "us.amazon.nova-micro-v1:0")

    agent = build_agent()

    assert agent.model.get_config()["model_id"] == "us.amazon.nova-micro-v1:0"


def test_agent_has_exactly_one_read_only_tool() -> None:
    agent = build_agent()

    assert list(agent.tool_names) == ["search_portfolio"]


def test_system_prompt_is_the_shared_prompt() -> None:
    from agent.prompts import SYSTEM_PROMPT

    agent = build_agent()

    assert agent.system_prompt == SYSTEM_PROMPT


def test_missing_model_id_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_ID")

    with pytest.raises(ConfigError):
        build_agent()


def test_missing_knowledge_base_id_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KNOWLEDGE_BASE_ID")

    with pytest.raises(ConfigError):
        build_agent()


def test_bedrock_model_receives_configured_max_tokens_and_temperature(monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent-runtime` spec-adjacent hardening: generation must be bounded,
    not left at the SDK's own defaults (MEDIUM security finding)."""
    monkeypatch.setenv("MAX_TOKENS", "256")
    monkeypatch.setenv("TEMPERATURE", "0.9")
    monkeypatch.setattr(agent_factory_module, "BedrockModel", _FakeBedrockModel)

    agent = build_agent()

    assert agent.model.kwargs["max_tokens"] == 256
    assert agent.model.kwargs["temperature"] == 0.9


def test_bedrock_model_receives_default_max_tokens_and_temperature_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_factory_module, "BedrockModel", _FakeBedrockModel)

    agent = build_agent()

    assert agent.model.kwargs["max_tokens"] == 512
    assert agent.model.kwargs["temperature"] == 0.2
