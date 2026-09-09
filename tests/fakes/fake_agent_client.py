"""In-memory `AgentClient` test double. Records calls; can raise a scripted error."""

from __future__ import annotations

from dataclasses import dataclass, field

from api.domain.models import AgentAnswer


@dataclass
class FakeAgentClient:
    """Returns a fixed answer, or raises `error` if set."""

    answer: AgentAnswer = field(default_factory=lambda: AgentAnswer(answer="stub answer", language="en"))
    error: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def ask(self, prompt: str, runtime_session_id: str) -> AgentAnswer:
        self.calls.append((prompt, runtime_session_id))
        if self.error is not None:
            raise self.error
        return self.answer
