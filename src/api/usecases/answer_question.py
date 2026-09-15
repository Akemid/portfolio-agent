"""The chat use case (design.md §4): validate -> session -> rate limits -> invoke -> map.

The only place this order is written. Depends on domain types and `Protocol` ports
only — no AWS SDK import, no HTTP concept — so every path runs against the in-memory
fakes in `tests/fakes/` with zero I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.domain.errors import RateLimited
from api.domain.hashing import derive_key
from api.domain.models import AgentAnswer, ChatRequest, Session
from api.domain.session_identity import decide_session
from api.ports.agent_client import AgentClient
from api.ports.clock import Clock
from api.ports.rate_limiter import RateLimiter
from api.ports.session_store import SessionStore


@dataclass(frozen=True)
class AnswerResult:
    """Everything the HTTP layer (`api.http.responder`) needs to build a response."""

    answer: AgentAnswer
    session: Session
    session_is_new: bool


def answer_question(
    message: str,
    cookie_value: str | None,
    source_ip: str,
    *,
    session_store: SessionStore,
    rate_limiter: RateLimiter,
    agent_client: AgentClient,
    clock: Clock,
) -> AnswerResult:
    """Run the ordered chat pipeline for one request.

    Raises `ValidationError` for an invalid message, before any port is touched.
    Raises `RateLimited` (either scope) when either counter is exceeded — the agent
    is never invoked in that case (`rate-limiting` spec, *Check-and-Increment Before
    Invocation*). Propagates whatever `agent_client.ask` raises on failure
    (`UpstreamError`/`UpstreamTimeout` by the `AgentClient` port contract).
    """
    request = ChatRequest(message=message)
    session, session_is_new = decide_session(cookie_value, session_store, clock)

    decision = rate_limiter.check_and_increment(
        derive_key("db", session.session_id),
        derive_key("ip", source_ip),
    )
    if not decision.allowed:
        if decision.scope is None or decision.retry_after_seconds is None:
            raise ValueError("a denied RateLimitDecision must carry scope and retry_after_seconds")
        raise RateLimited(scope=decision.scope, retry_after_seconds=decision.retry_after_seconds)

    runtime_session_id = derive_key("rt", session.session_id)
    answer = agent_client.ask(request.message, runtime_session_id)

    return AnswerResult(answer=answer, session=session, session_is_new=session_is_new)
