"""AgentCore invocation port (`agent-runtime` spec)."""

from __future__ import annotations

from typing import Protocol

from api.domain.models import AgentAnswer


class AgentClient(Protocol):
    """Invokes the hosted agent. MUST raise `UpstreamError`/`UpstreamTimeout` on failure."""

    def ask(self, prompt: str, runtime_session_id: str) -> AgentAnswer:
        """Invoke the agent runtime with `prompt` and return its answer."""
        ...
