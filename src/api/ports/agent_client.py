"""AgentCore invocation port (`agent-runtime` spec)."""

from __future__ import annotations

from typing import Protocol

from api.domain.models import AgentAnswer


class AgentClient(Protocol):
    """Invokes the hosted agent and returns its answer.

    Implementations MUST raise `api.domain.errors.UpstreamError` or
    `UpstreamTimeout` on failure — never leak the underlying AWS exception.
    """

    def ask(self, prompt: str, runtime_session_id: str) -> AgentAnswer:
        """Invoke the agent runtime with `prompt` and return its answer."""
        ...
