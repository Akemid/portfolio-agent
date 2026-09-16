"""Environment-sourced agent configuration (design.md §5, §8 RQ-1).

Mirrors `api.config.Settings`' fail-fast shape, but is a fully separate class
in its own module: `src/agent` is a different deployable from `src/api` and
must never import it (`tests/unit/test_lambda_package_isolation.py`).
`AgentSettings.from_env` reads an injected mapping, never `os.environ`
directly, so config resolution stays pure and unit-testable —
`agent_factory.build_agent` is the only caller that passes the real
`os.environ`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

_DEFAULT_AWS_REGION = "us-east-1"
# design.md §2.1's sequence diagram retrieves the "top 4 chunks"; kept
# configurable so a corpus-size change needs no code change.
_DEFAULT_RETRIEVAL_TOP_K = 4
_MIN_RETRIEVAL_TOP_K = 1
_MAX_RETRIEVAL_TOP_K = 10

# Output-bounding defaults (MAJOR/MEDIUM security finding: an unbounded
# generation is both a cost/availability risk and a bigger prompt-injection
# payoff surface). Verified against the Strands `BedrockModel` constructor,
# which accepts `max_tokens` and `temperature` directly
# (https://github.com/strands-agents/docs/blob/main/site/src/content/docs/user-guide/deploy/operating-agents-in-production.mdx,
# https://github.com/strands-agents/docs/blob/main/site/src/content/docs/user-guide/concepts/model-providers/amazon-bedrock.mdx,
# fetched via Context7 `/strands-agents/docs`, 2026-09-15).
_DEFAULT_MAX_TOKENS = 512
_MIN_MAX_TOKENS = 64
_MAX_MAX_TOKENS = 2048

_DEFAULT_TEMPERATURE = 0.2
_MIN_TEMPERATURE = 0.0
_MAX_TEMPERATURE = 1.0

# Hard cap on the parsed `answer` string (`agent.main._parse_model_output`).
# Exposed as a public constant (and via `max_answer_chars_from_env` below)
# because `agent.main` needs it even when `build_agent` — and therefore the
# rest of `AgentSettings` — is faked out entirely in unit tests.
DEFAULT_MAX_ANSWER_CHARS = 1200


class ConfigError(Exception):
    """Raised when required agent configuration is missing or malformed."""


@dataclass(frozen=True)
class AgentSettings:
    """Frozen agent configuration, resolved once per `build_agent()` call."""

    model_id: str
    knowledge_base_id: str
    aws_region: str
    retrieval_top_k: int
    max_tokens: int
    temperature: float

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> AgentSettings:
        """Build `AgentSettings` from an injected environment mapping.

        `model_id` MUST be supplied through configuration, never hardcoded —
        `agent-runtime` spec, *Foundation Model* — so switching to the
        `us.amazon.nova-micro-v1:0` inference profile (design.md §8 RQ-1)
        needs no code change.
        """
        return cls(
            model_id=_require(environ, "MODEL_ID"),
            knowledge_base_id=_require(environ, "KNOWLEDGE_BASE_ID"),
            aws_region=environ.get("AWS_REGION") or _DEFAULT_AWS_REGION,
            retrieval_top_k=_optional_int(
                environ,
                "RETRIEVAL_TOP_K",
                _DEFAULT_RETRIEVAL_TOP_K,
                minimum=_MIN_RETRIEVAL_TOP_K,
                maximum=_MAX_RETRIEVAL_TOP_K,
            ),
            max_tokens=_optional_int(
                environ, "MAX_TOKENS", _DEFAULT_MAX_TOKENS, minimum=_MIN_MAX_TOKENS, maximum=_MAX_MAX_TOKENS
            ),
            temperature=_optional_float(
                environ, "TEMPERATURE", _DEFAULT_TEMPERATURE, minimum=_MIN_TEMPERATURE, maximum=_MAX_TEMPERATURE
            ),
        )


def max_answer_chars_from_env(environ: Mapping[str, str]) -> int:
    """Read `MAX_ANSWER_CHARS`, independent of `AgentSettings.from_env`.

    `agent.main.agent_invocation` needs this value on every path, including
    unit tests that monkeypatch `build_agent` wholesale and never set the
    required `MODEL_ID`/`KNOWLEDGE_BASE_ID` — going through the full
    `AgentSettings.from_env` here would make those tests fail on unrelated
    missing configuration.
    """
    return _optional_int(environ, "MAX_ANSWER_CHARS", DEFAULT_MAX_ANSWER_CHARS, minimum=1)


def _require(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def _optional_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = environ.get(name)
    if not raw:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    _check_range(name, value, minimum, maximum)
    return value


def _optional_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = environ.get(name)
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    _check_range(name, value, minimum, maximum)
    return value


def _check_range(name: str, value: float, minimum: float | None, maximum: float | None) -> None:
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value!r}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}, got {value!r}")
