"""Tests for `agent.agent_factory.build_agent` (design.md §5; `agent-runtime`
spec, *Foundation Model*, *No Side-Effect Tools*).

No network: constructing a `BedrockModel` or a `boto3` client only builds a
local object — no call is made until the agent is actually invoked, which
these tests never do.
"""

from __future__ import annotations

import pytest

from agent.agent_factory import build_agent
from agent.settings import ConfigError


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
