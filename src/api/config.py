"""Environment-sourced Lambda configuration (design.md SS4 composition root).

`Settings.from_env` reads an injected `environ` mapping, never `os.environ`
directly, so config resolution stays a pure, unit-testable function —
`handler.py` is the only caller that passes the real `os.environ`. A missing
required variable or a malformed value raises `ConfigError` immediately, so a
misconfigured Lambda fails at cold start instead of on the first request
(`design.md` composition-root fail-fast contract).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

_DEFAULT_AWS_REGION = "us-east-1"
# `chat-endpoint` spec, *CORS Restriction*: production origin plus one
# env-configurable localhost origin. `ALLOWED_ORIGINS` is a single
# comma-separated knob so local dev appends its origin without touching code.
_DEFAULT_ALLOWED_ORIGINS = ("https://sergiomondragon.com",)
_DEFAULT_SESSION_DAILY_LIMIT = 10
_DEFAULT_IP_MINUTE_LIMIT = 5
_DEFAULT_COOKIE_SECURE = True
_DEFAULT_LOG_LEVEL = "INFO"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class ConfigError(Exception):
    """Raised when required Lambda configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    """Frozen Lambda configuration, resolved once at cold start."""

    table_name: str
    agent_runtime_arn: str
    agent_qualifier: str | None
    aws_region: str
    allowed_origins: tuple[str, ...]
    session_daily_limit: int
    ip_minute_limit: int
    cookie_secure: bool
    log_level: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Settings:
        """Build `Settings` from an injected environment mapping."""
        return cls(
            table_name=_require(environ, "TABLE_NAME"),
            agent_runtime_arn=_require(environ, "AGENT_RUNTIME_ARN"),
            agent_qualifier=environ.get("AGENT_QUALIFIER") or None,
            aws_region=environ.get("AWS_REGION") or _DEFAULT_AWS_REGION,
            allowed_origins=_split_origins(environ.get("ALLOWED_ORIGINS")),
            session_daily_limit=_optional_int(environ, "SESSION_DAILY_LIMIT", _DEFAULT_SESSION_DAILY_LIMIT),
            ip_minute_limit=_optional_int(environ, "IP_MINUTE_LIMIT", _DEFAULT_IP_MINUTE_LIMIT),
            cookie_secure=_optional_bool(environ, "COOKIE_SECURE", _DEFAULT_COOKIE_SECURE),
            log_level=environ.get("LOG_LEVEL") or _DEFAULT_LOG_LEVEL,
        )


def _require(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def _split_origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return _DEFAULT_ALLOWED_ORIGINS
    origins = tuple(origin.strip() for origin in raw.split(",") if origin.strip())
    return origins or _DEFAULT_ALLOWED_ORIGINS


def _optional_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _optional_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name)
    if not raw:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be a boolean-like value, got {raw!r}")
