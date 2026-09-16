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


class ConfigError(Exception):
    """Raised when required agent configuration is missing or malformed."""


@dataclass(frozen=True)
class AgentSettings:
    """Frozen agent configuration, resolved once per `build_agent()` call."""

    model_id: str
    knowledge_base_id: str
    aws_region: str
    retrieval_top_k: int

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
            retrieval_top_k=_optional_int(environ, "RETRIEVAL_TOP_K", _DEFAULT_RETRIEVAL_TOP_K),
        )


def _require(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name)
    if not value:
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def _optional_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
