"""boto3 `bedrock-agentcore` adapter for the `AgentClient` port (design.md SS4, SS6).

Calls `InvokeAgentRuntime` over SigV4 — the only way the Lambda gate is allowed to
reach the hosted agent (`agent-runtime` spec, *Hosting and Invocation*). API
reference: https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html
Usage example: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-invoke-agent.html
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

from api.domain.errors import UpstreamError, UpstreamTimeout
from api.domain.models import SUPPORTED_LANGUAGES, AgentAnswer

_CONTENT_TYPE = "application/json"

# Generic, log-safe messages — never echo the raw exception, response body, ARN,
# or any id back to the caller (chat-endpoint spec, *Upstream Failure Mapping*).
_UNAVAILABLE_MESSAGE = "the agent is temporarily unavailable"
_TIMEOUT_MESSAGE = "the agent did not respond in time"

# Cost- and latency-aware defaults (design.md SS7 RQ-5): the AgentCore invocation
# is the single most expensive and slowest call in the request path, so a client
# retry would both double the token spend for one visitor question and risk
# blowing the end-to-end p95 budget. `total_max_attempts=1` means "no automatic
# retry" — the use case's own error mapping is the only retry surface, and there
# is none. `total_max_attempts` (standard retry mode) counts the initial call
# plus retries, unlike the legacy-mode `max_attempts` key, which counts retries
# only and would need `max_attempts=0` for the same "no retry" effect.
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 3.0
_DEFAULT_READ_TIMEOUT_SECONDS = 12.0
_DEFAULT_TOTAL_MAX_ATTEMPTS = 1


class AgentCoreClient:
    """Invokes a hosted Strands agent via AgentCore Runtime's `InvokeAgentRuntime`.

    `client` is an already-configured boto3 `bedrock-agentcore` client, injected by
    the composition root — never created here (design.md SS4, hexagonal boundary:
    adapters depend on ports, the composition root wires the SDK).
    """

    def __init__(self, client: Any, agent_runtime_arn: str, qualifier: str | None = None) -> None:
        self._client = client
        self._agent_runtime_arn = agent_runtime_arn
        self._qualifier = qualifier

    def ask(self, prompt: str, runtime_session_id: str) -> AgentAnswer:
        """Invoke the runtime and map its response to an `AgentAnswer`.

        `runtime_session_id` is expected to already be the domain-derived,
        hashed id (`derive_key("rt", session_id)`, design.md SS4.1) — this
        adapter never sees or derives the raw session id.

        Raises `UpstreamTimeout` when the call exceeds the client's configured
        timeouts, `UpstreamError` for any other failure — including a
        malformed, incomplete, or unsupported-language response body. Every
        raised message is a fixed, generic string: the original exception is
        chained (`from exc`) for the caller to log with `exc_info`, never
        rendered into the message itself.
        """
        kwargs: dict[str, Any] = {
            "agentRuntimeArn": self._agent_runtime_arn,
            "runtimeSessionId": runtime_session_id,
            # `language_hint` is reserved for a future client-side hint (design.md
            # SS6): v1 always sends `null` and the agent always detects the language
            # itself, so adding the hint later is not a contract break.
            "payload": json.dumps({"prompt": prompt, "language_hint": None}).encode(),
            "contentType": _CONTENT_TYPE,
            "accept": _CONTENT_TYPE,
        }
        if self._qualifier is not None:
            kwargs["qualifier"] = self._qualifier

        try:
            response = self._client.invoke_agent_runtime(**kwargs)
            return self._parse_answer(response)
        except (ReadTimeoutError, ConnectTimeoutError) as exc:
            # Covers both the initial call and `StreamingBody.read()`, which can raise
            # the same timeout errors mid-stream once the response has started.
            raise UpstreamTimeout(_TIMEOUT_MESSAGE) from exc
        except (ClientError, BotoCoreError) as exc:
            # Covers both the initial call and any other `BotoCoreError` raised while
            # reading the streaming body (e.g. `IncompleteReadError`, `ResponseStreamingError`).
            raise UpstreamError(_UNAVAILABLE_MESSAGE) from exc

    def _parse_answer(self, response: Any) -> AgentAnswer:
        content_type = response.get("contentType") if isinstance(response, dict) else None
        if content_type is not None and not content_type.startswith(_CONTENT_TYPE):
            raise UpstreamError(_UNAVAILABLE_MESSAGE)

        try:
            raw_body = response["response"].read()
        except (KeyError, AttributeError, TypeError) as exc:
            raise UpstreamError(_UNAVAILABLE_MESSAGE) from exc

        try:
            data = json.loads(raw_body)
        except (TypeError, ValueError) as exc:
            raise UpstreamError(_UNAVAILABLE_MESSAGE) from exc

        if not isinstance(data, dict):
            raise UpstreamError(_UNAVAILABLE_MESSAGE)

        answer = data.get("answer")
        language = data.get("language")
        if not isinstance(answer, str) or language not in SUPPORTED_LANGUAGES:
            raise UpstreamError(_UNAVAILABLE_MESSAGE)

        return AgentAnswer(answer=answer, language=language)


def build_agentcore_client(
    region: str,
    connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = _DEFAULT_READ_TIMEOUT_SECONDS,
) -> Any:
    """Build a boto3 `bedrock-agentcore` client with a cost- and latency-aware `Config`.

    Not called at import time — the composition root (a later batch) calls this
    once, at Lambda module scope, and reuses the client across warm invocations.

    `read_timeout` defaults to 12s: design.md SS7 RQ-5 estimates a cold-session
    invocation at ~6-12s, and this must stay comfortably under both the Lambda
    function timeout and the API Gateway HTTP API integration timeout (30s) —
    the composition root's own timeout is set with headroom above this value.
    Retries are disabled (`total_max_attempts=1`, standard mode): retrying a
    non-idempotent, billed agent invocation would double cost per question,
    which the design's cost ceiling (SS7) explicitly guards against.
    """
    config = Config(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries={"total_max_attempts": _DEFAULT_TOTAL_MAX_ATTEMPTS, "mode": "standard"},
    )
    return boto3.client("bedrock-agentcore", region_name=region, config=config)
