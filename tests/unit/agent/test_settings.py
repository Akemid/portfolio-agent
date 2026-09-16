"""Tests for `agent.settings.AgentSettings.from_env` (design.md §5, §8 RQ-1 —
the model id is an env var, not a constant, so switching to the
`us.amazon.nova-micro-v1:0` inference profile needs no code change)."""

from __future__ import annotations

import pytest

from agent.settings import AgentSettings, ConfigError

_REQUIRED_ENV = {
    "MODEL_ID": "amazon.nova-micro-v1:0",
    "KNOWLEDGE_BASE_ID": "KB0000000000",
}


def test_reads_required_and_defaulted_values() -> None:
    settings = AgentSettings.from_env({**_REQUIRED_ENV, "AWS_REGION": "us-west-2", "RETRIEVAL_TOP_K": "8"})

    assert settings.model_id == "amazon.nova-micro-v1:0"
    assert settings.knowledge_base_id == "KB0000000000"
    assert settings.aws_region == "us-west-2"
    assert settings.retrieval_top_k == 8


def test_defaults_region_and_top_k_when_unset() -> None:
    settings = AgentSettings.from_env(_REQUIRED_ENV)

    assert settings.aws_region == "us-east-1"
    assert settings.retrieval_top_k == 4


@pytest.mark.parametrize("missing", ["MODEL_ID", "KNOWLEDGE_BASE_ID"])
def test_missing_required_var_raises_config_error(missing: str) -> None:
    env = {k: v for k, v in _REQUIRED_ENV.items() if k != missing}

    with pytest.raises(ConfigError):
        AgentSettings.from_env(env)


def test_non_integer_top_k_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        AgentSettings.from_env({**_REQUIRED_ENV, "RETRIEVAL_TOP_K": "not-a-number"})
