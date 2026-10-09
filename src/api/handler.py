"""Lambda entrypoint and composition root (design.md §4).

Wires every adapter once, at module scope, and reuses it across warm
invocations — the module-level `_container` cache is what makes a warm Lambda
avoid re-creating a boto3 client or a DynamoDB `Table` resource on every
request. `build_container` is the pure factory: tests call it (or construct a
`Container` directly) with fakes injected, so no test ever needs a real AWS
call.

CORS preflight (`OPTIONS /v1/chat`) is handled by API Gateway HTTP API's own
CORS configuration (a Phase 8 CDK setting), never by this handler — the
handler only ever sees a `POST /v1/chat` invocation and always echoes the
allowlisted-origin headers `cors_headers` builds into every response.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import boto3

from api.adapters.agentcore_client import AgentCoreClient, build_agentcore_client
from api.adapters.dynamo_rate_limiter import DynamoRateLimiter
from api.adapters.dynamo_session_store import DynamoSessionStore
from api.adapters.secure_ids import SecureIds
from api.adapters.system_clock import SystemClock
from api.config import Settings
from api.domain.errors import RateLimited, UpstreamError, UpstreamTimeout, ValidationError
from api.http.request_parser import IncomingRequest, extract_message, extract_origin, parse_event
from api.http.responder import error_response, success_response
from api.observability import hash_for_log, truncate
from api.ports.agent_client import AgentClient
from api.ports.clock import Clock
from api.ports.ids import Ids
from api.ports.rate_limiter import RateLimiter
from api.ports.session_store import SessionStore
from api.usecases.answer_question import answer_question

logger = logging.getLogger(__name__)

# A cookie-less request has no session identity yet; this fixed, non-secret
# sentinel is still hashed before logging so the log line format never
# depends on whether a cookie was present (chat-endpoint spec, *Log Redaction*).
_NO_SESSION_LOG_ID = "no-session"

# Domain errors `answer_question`/`extract_message` can raise — anything else
# is unexpected and gets a full traceback in CloudWatch (never in the body).
_KNOWN_DOMAIN_ERRORS = (ValidationError, RateLimited, UpstreamError, UpstreamTimeout)

# Fixed, non-PII reason code logged when `requestContext.http.sourceIp` is
# missing or blank — never the raw event contents (`rate-limiting` spec,
# *Header Spoofing Attempt*: an empty derived key must never collapse
# unrelated visitors into one shared rate-limit bucket).
_MISSING_SOURCE_IP_REASON = "missing_source_ip"


@dataclass(frozen=True)
class Container:
    """Wired ports for one warm Lambda execution environment."""

    session_store: SessionStore
    rate_limiter: RateLimiter
    agent_client: AgentClient
    clock: Clock
    allowed_origins: tuple[str, ...]
    cookie_secure: bool = True


_container: Container | None = None


def build_container(
    settings: Settings,
    *,
    table: Any = None,
    agent_client: AgentClient | None = None,
    clock: Clock | None = None,
    ids: Ids | None = None,
) -> Container:
    """Wire real adapters from `settings`; accept fakes for testing."""
    resolved_clock: Clock = clock or SystemClock()
    resolved_ids: Ids = ids or SecureIds()
    if table is not None:
        resolved_table = table
    else:
        resolved_table = boto3.resource("dynamodb", region_name=settings.aws_region).Table(settings.table_name)
    resolved_agent_client: AgentClient = agent_client or AgentCoreClient(
        build_agentcore_client(settings.aws_region),
        settings.agent_runtime_arn,
        settings.agent_qualifier,
    )
    return Container(
        session_store=DynamoSessionStore(resolved_table, resolved_clock, resolved_ids),
        rate_limiter=DynamoRateLimiter(
            resolved_table, resolved_clock, settings.session_daily_limit, settings.ip_minute_limit
        ),
        agent_client=resolved_agent_client,
        clock=resolved_clock,
        allowed_origins=settings.allowed_origins,
        cookie_secure=settings.cookie_secure,
    )


def _get_container() -> Container:
    global _container
    if _container is None:
        settings = Settings.from_env(os.environ)
        logger.setLevel(settings.log_level)
        _container = build_container(settings)
    return _container


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Handle one `POST /v1/chat` invocation: parse -> use case -> respond -> log."""
    container = _get_container()
    started = time.perf_counter()
    request: IncomingRequest | None = None
    session_id_for_log = _NO_SESSION_LOG_ID
    message_for_log = ""
    scope: str | None = None

    try:
        request = parse_event(event)
        session_id_for_log = request.cookie_value or _NO_SESSION_LOG_ID
        source_ip = request.source_ip
        if source_ip is None:
            logger.warning(_MISSING_SOURCE_IP_REASON)
            raise ValidationError("source IP is required")
        message = extract_message(request.body)
        message_for_log = message
        result = answer_question(
            message,
            request.cookie_value,
            source_ip,
            session_store=container.session_store,
            rate_limiter=container.rate_limiter,
            agent_client=container.agent_client,
            clock=container.clock,
        )
        session_id_for_log = result.session.session_id
        response = success_response(
            result,
            origin=request.origin,
            allowlist=container.allowed_origins,
            cookie_secure=container.cookie_secure,
        )
    except Exception as exc:
        scope = getattr(exc, "scope", None)
        origin = request.origin if request is not None else extract_origin(event)
        response = error_response(exc, origin=origin, allowlist=container.allowed_origins)
        if not message_for_log and request is not None:
            message_for_log = request.body or ""
        if not isinstance(exc, _KNOWN_DOMAIN_ERRORS):
            logger.exception("unhandled error in lambda_handler")

    latency_ms = int((time.perf_counter() - started) * 1000)
    _log_request(
        status=response["statusCode"],
        scope=scope,
        session_id=session_id_for_log,
        message=message_for_log,
        latency_ms=latency_ms,
    )
    return response


def _log_request(*, status: int, scope: str | None, session_id: str, message: str, latency_ms: int) -> None:
    """Emit one structured JSON log line — never a raw session id, IP, or full message.

    `json.dumps` produces a single line with no embedded newlines, so a
    crafted message body can never forge a second, spoofed log line
    (`chat-endpoint` spec, *Log Redaction*).
    """
    log_event = {
        "status": status,
        "scope": scope,
        "session": hash_for_log(session_id),
        "message_prefix": truncate(message),
        "latency_ms": latency_ms,
    }
    logger.info(json.dumps(log_event))
