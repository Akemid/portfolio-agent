"""Unit tests for `api.config.Settings` (`rate-limiting` spec, thresholds MUST be
configurable via an environment variable; design.md SS4 composition root)."""

from __future__ import annotations

import pytest

from api.config import ConfigError, Settings

_REQUIRED_ENV = {
    "TABLE_NAME": "portfolio-agent-sessions",
    "AGENT_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test-agent",
}


def test_settings_from_env_reads_all_required_vars() -> None:
    settings = Settings.from_env(_REQUIRED_ENV)

    assert settings.table_name == "portfolio-agent-sessions"
    assert settings.agent_runtime_arn == "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test-agent"


def test_missing_table_name_raises_config_error() -> None:
    env = {k: v for k, v in _REQUIRED_ENV.items() if k != "TABLE_NAME"}

    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_missing_agent_runtime_arn_raises_config_error() -> None:
    env = {k: v for k, v in _REQUIRED_ENV.items() if k != "AGENT_RUNTIME_ARN"}

    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_defaults_when_optional_vars_are_absent() -> None:
    settings = Settings.from_env(_REQUIRED_ENV)

    assert settings.agent_qualifier is None
    assert settings.aws_region == "us-east-1"
    assert settings.allowed_origins == ("https://sergiomondragon.com",)
    assert settings.session_daily_limit == 10
    assert settings.ip_minute_limit == 5
    assert settings.cookie_secure is True
    assert settings.log_level == "INFO"


def test_overrides_are_read_from_env() -> None:
    env = {
        **_REQUIRED_ENV,
        "AGENT_QUALIFIER": "PROD",
        "AWS_REGION": "us-west-2",
        "ALLOWED_ORIGINS": "https://sergiomondragon.com,http://localhost:4321",
        "SESSION_DAILY_LIMIT": "25",
        "IP_MINUTE_LIMIT": "8",
        "COOKIE_SECURE": "false",
        "LOG_LEVEL": "DEBUG",
    }

    settings = Settings.from_env(env)

    assert settings.agent_qualifier == "PROD"
    assert settings.aws_region == "us-west-2"
    assert settings.allowed_origins == ("https://sergiomondragon.com", "http://localhost:4321")
    assert settings.session_daily_limit == 25
    assert settings.ip_minute_limit == 8
    assert settings.cookie_secure is False
    assert settings.log_level == "DEBUG"


def test_allowed_origins_falls_back_to_default_when_only_commas_and_whitespace() -> None:
    env = {**_REQUIRED_ENV, "ALLOWED_ORIGINS": " , , "}

    settings = Settings.from_env(env)

    assert settings.allowed_origins == ("https://sergiomondragon.com",)


def test_bad_session_daily_limit_raises_config_error() -> None:
    env = {**_REQUIRED_ENV, "SESSION_DAILY_LIMIT": "not-a-number"}

    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_bad_ip_minute_limit_raises_config_error() -> None:
    env = {**_REQUIRED_ENV, "IP_MINUTE_LIMIT": "not-a-number"}

    with pytest.raises(ConfigError):
        Settings.from_env(env)


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE"])
def test_cookie_secure_accepts_truthy_string_values(value: str) -> None:
    settings = Settings.from_env({**_REQUIRED_ENV, "COOKIE_SECURE": value})

    assert settings.cookie_secure is True


def test_bad_cookie_secure_raises_config_error() -> None:
    env = {**_REQUIRED_ENV, "COOKIE_SECURE": "maybe"}

    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_settings_is_frozen() -> None:
    settings = Settings.from_env(_REQUIRED_ENV)

    with pytest.raises(AttributeError):
        settings.table_name = "other"  # type: ignore[misc]
