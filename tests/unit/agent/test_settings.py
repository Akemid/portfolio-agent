"""Tests for `agent.settings.AgentSettings.from_env` (design.md §5, §8 RQ-1 —
the model id is an env var, not a constant, so switching to the
`us.amazon.nova-micro-v1:0` inference profile needs no code change)."""

from __future__ import annotations

import pytest

from agent.settings import DEFAULT_MAX_ANSWER_CHARS, AgentSettings, ConfigError, max_answer_chars_from_env

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


def test_reads_max_tokens_and_temperature_from_env() -> None:
    settings = AgentSettings.from_env({**_REQUIRED_ENV, "MAX_TOKENS": "1024", "TEMPERATURE": "0.7"})

    assert settings.max_tokens == 1024
    assert settings.temperature == 0.7


def test_defaults_max_tokens_and_temperature_when_unset() -> None:
    settings = AgentSettings.from_env(_REQUIRED_ENV)

    assert settings.max_tokens == 512
    assert settings.temperature == 0.2


def test_non_integer_max_tokens_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        AgentSettings.from_env({**_REQUIRED_ENV, "MAX_TOKENS": "not-a-number"})


def test_non_numeric_temperature_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        AgentSettings.from_env({**_REQUIRED_ENV, "TEMPERATURE": "not-a-number"})


@pytest.mark.parametrize("out_of_range", ["63", "2049"])
def test_max_tokens_outside_64_to_2048_raises_config_error(out_of_range: str) -> None:
    with pytest.raises(ConfigError):
        AgentSettings.from_env({**_REQUIRED_ENV, "MAX_TOKENS": out_of_range})


@pytest.mark.parametrize("boundary", ["64", "2048"])
def test_max_tokens_at_the_boundary_is_accepted(boundary: str) -> None:
    settings = AgentSettings.from_env({**_REQUIRED_ENV, "MAX_TOKENS": boundary})

    assert settings.max_tokens == int(boundary)


@pytest.mark.parametrize("out_of_range", ["-0.01", "1.01"])
def test_temperature_outside_0_to_1_raises_config_error(out_of_range: str) -> None:
    with pytest.raises(ConfigError):
        AgentSettings.from_env({**_REQUIRED_ENV, "TEMPERATURE": out_of_range})


@pytest.mark.parametrize("boundary", ["0", "1"])
def test_temperature_at_the_boundary_is_accepted(boundary: str) -> None:
    settings = AgentSettings.from_env({**_REQUIRED_ENV, "TEMPERATURE": boundary})

    assert settings.temperature == float(boundary)


def test_max_answer_chars_from_env_defaults_to_1200_when_unset() -> None:
    """Read independently of `AgentSettings.from_env` (`agent.main` needs this
    even in unit tests that fake out `build_agent` entirely and never set
    `MODEL_ID`/`KNOWLEDGE_BASE_ID`)."""
    assert max_answer_chars_from_env({}) == DEFAULT_MAX_ANSWER_CHARS == 1200


def test_max_answer_chars_from_env_reads_override() -> None:
    assert max_answer_chars_from_env({"MAX_ANSWER_CHARS": "500"}) == 500


def test_max_answer_chars_from_env_non_integer_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        max_answer_chars_from_env({"MAX_ANSWER_CHARS": "not-a-number"})
