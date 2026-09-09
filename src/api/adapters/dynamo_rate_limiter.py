"""DynamoDB-backed `RateLimiter` adapter (design.md SS4.2, `rate-limiting` spec).

Checks and atomically increments two independent counters, **IP first**: a
per-IP weighted sliding-window counter, then a per-session fixed daily-UTC
counter. IP is checked first because it is the cheaper signal to reject
abuse on — one counter stops a single attacker regardless of how many
session cookies they forge — so a denied IP never touches the session
counter at all (design.md SS4.2, *Rate-limited path*).

Both counters use a single conditional `update_item` each: the
`ConditionExpression` and the `ADD` happen in the same atomic DynamoDB call,
so a burst of concurrent requests cannot slip past the limit (no
read-then-write race). The literal `attribute_not_exists(#c) OR #c <
:limit` condition is design.md SS4.2's own worked example for the session
cap; the IP cap reuses the same shape against a threshold *derived* from the
weighted estimate below, because DynamoDB's `ConditionExpression` grammar has
no `if_not_exists`-style function to compare against "0 when absent" (that
function only exists in `UpdateExpression`). The one-time cost of this
escape hatch is that the very first write into a brand-new bucket always
succeeds regardless of the derived threshold — required so a bucket can ever
be born at all — but every write after that is checked against the real
stored count.

**IP cap — weighted sliding window** (design.md SS4.2). A plain fixed
60-second bucket was explicitly rejected: it lets up to 2x the configured
limit through across a minute boundary (5 old + 5 fresh). Instead, a `Query`
reads the previous minute's bucket and the estimate

    prev_count * (1 - elapsed_fraction_of_current_minute) + curr_count

approximates the true rolling window. The current bucket's write is then
conditioned on `curr_count < (limit - prev_weight)` — algebraically
equivalent to "estimate < limit" evaluated before the increment.

`session_key` and `ip_key` are already-hashed identifiers
(`derive_key("db", session_id)` / `derive_key("ip", source_ip)` — design.md
SS4.1), computed by the caller. This adapter never sees, hashes, or logs a
raw session id or IP address.

**No rollback on agent failure (v1).** Neither counter is decremented if the
downstream agent invocation fails after being invoked: a question that
reaches the agent but gets a failed answer still counts against both
limits. This keeps each scope a single atomic write (no compensating
decrement, no distributed transaction) at the cost of occasionally
under-serving a legitimate user who hit a transient upstream error —
acceptable for a personal-portfolio bot at this scale.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Any

from api.adapters.dynamo_keys import (
    PARTITION_KEY,
    SORT_KEY,
    TTL_ATTRIBUTE,
    ip_minute_sk,
    ip_pk,
    session_day_sk,
    session_pk,
)
from api.domain.models import RateLimitDecision
from api.ports.clock import Clock

_COUNT_ATTRIBUTE = "count"

_DEFAULT_SESSION_DAILY_LIMIT = 10
_DEFAULT_IP_MINUTE_LIMIT = 5

_SESSION_TTL_GRACE_SECONDS = 300
_IP_TTL_GRACE_SECONDS = 180

_SECONDS_PER_MINUTE = 60


class DynamoRateLimiter:
    """Checks and atomically increments both counters before any agent invocation.

    `table` is a boto3 DynamoDB `Table` resource, injected by the
    composition root — never created here (design.md SS4, hexagonal
    boundary). `session_daily_limit` and `ip_minute_limit` default to the
    spec's values but are constructor-injected so the Lambda composition
    root (PR4) can wire them from environment variables (`rate-limiting`
    spec: "threshold MUST be configurable via an environment variable").
    """

    def __init__(
        self,
        table: Any,
        clock: Clock,
        session_daily_limit: int = _DEFAULT_SESSION_DAILY_LIMIT,
        ip_minute_limit: int = _DEFAULT_IP_MINUTE_LIMIT,
    ) -> None:
        self._table = table
        self._clock = clock
        self._session_daily_limit = session_daily_limit
        self._ip_minute_limit = ip_minute_limit

    def check_and_increment(self, session_key: str, ip_key: str) -> RateLimitDecision:
        """Return whether the request is allowed under both rate limits.

        Checks the IP limit first: if it denies, the session counter is
        never touched.

        Both keys MUST be non-empty derived keys (`derive_key(...)` output). An
        empty key would collapse unrelated visitors into one shared counter, so
        it is rejected loudly instead of silently degrading isolation.
        """
        if not session_key or not ip_key:
            raise ValueError("rate-limit keys must be non-empty derived keys")
        now = self._clock.now()
        ip_decision = self._check_ip(ip_key, now)
        if not ip_decision.allowed:
            return ip_decision
        return self._check_session(session_key, now)

    def _check_session(self, session_key: str, now: datetime) -> RateLimitDecision:
        window_end = _next_utc_midnight(now)
        ttl = int(window_end.timestamp()) + _SESSION_TTL_GRACE_SECONDS
        try:
            self._table.update_item(
                Key={PARTITION_KEY: session_pk(session_key), SORT_KEY: session_day_sk(now.date())},
                UpdateExpression="SET #ttl = if_not_exists(#ttl, :ttl) ADD #c :one",
                ConditionExpression="attribute_not_exists(#c) OR #c < :limit",
                ExpressionAttributeNames={"#c": _COUNT_ATTRIBUTE, "#ttl": TTL_ATTRIBUTE},
                ExpressionAttributeValues={":one": 1, ":limit": self._session_daily_limit, ":ttl": ttl},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            retry_after = _seconds_until(window_end, now)
            return RateLimitDecision(allowed=False, scope="session", retry_after_seconds=retry_after)
        return RateLimitDecision(allowed=True)

    def _check_ip(self, ip_key: str, now: datetime) -> RateLimitDecision:
        pk = ip_pk(ip_key)
        curr_minute = now.replace(second=0, microsecond=0)
        prev_minute = curr_minute - timedelta(minutes=1)
        curr_sk = ip_minute_sk(curr_minute)
        prev_sk = ip_minute_sk(prev_minute)

        # Point read of the exact previous-minute bucket: cheaper than a Query and
        # keeps the Lambda IAM policy to Get/Put/UpdateItem. Strongly consistent
        # because a stale (lower) previous count would inflate the derived limit
        # right after a burst; the extra half RCU is negligible at portfolio traffic.
        response = self._table.get_item(Key={PARTITION_KEY: pk, SORT_KEY: prev_sk}, ConsistentRead=True)
        prev_item = response.get("Item")
        prev_count = int(prev_item[_COUNT_ATTRIBUTE]) if prev_item else 0
        derived_limit = Decimal(self._ip_minute_limit) - Decimal(prev_count) * (1 - _elapsed_fraction(now))

        window_end = curr_minute + timedelta(minutes=1)
        ttl = int(curr_minute.timestamp()) + _IP_TTL_GRACE_SECONDS
        try:
            self._table.update_item(
                Key={PARTITION_KEY: pk, SORT_KEY: curr_sk},
                UpdateExpression="SET #ttl = if_not_exists(#ttl, :ttl) ADD #c :one",
                ConditionExpression="attribute_not_exists(#c) OR #c < :derived_limit",
                ExpressionAttributeNames={"#c": _COUNT_ATTRIBUTE, "#ttl": TTL_ATTRIBUTE},
                ExpressionAttributeValues={":one": 1, ":derived_limit": derived_limit, ":ttl": ttl},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            retry_after = _seconds_until(window_end, now)
            return RateLimitDecision(allowed=False, scope="ip", retry_after_seconds=retry_after)
        return RateLimitDecision(allowed=True)


def _next_utc_midnight(now: datetime) -> datetime:
    tomorrow = now.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC)


def _elapsed_fraction(now: datetime) -> Decimal:
    """Return how far `now` is into its current minute, as a value in `[0, 1)`."""
    return (Decimal(now.second) + Decimal(now.microsecond) / Decimal(1_000_000)) / Decimal(_SECONDS_PER_MINUTE)


def _seconds_until(target: datetime, now: datetime) -> int:
    """Return whole seconds from `now` to `target`, never less than 1."""
    return max(1, ceil((target - now).total_seconds()))
