"""Single source of truth for the DynamoDB session item key shape (design.md SS4.2).

Both the real adapter (`dynamo_session_store.py`) and the test schema helper
(`tests/helpers/dynamodb_schema.py`) import the attribute names, prefix, and
key-builder from here so the key shape can never drift between production
code and test setup. `tests/` MAY import from `src/`; `src/` MUST NEVER
import from `tests/`.
"""

from __future__ import annotations

from datetime import date, datetime

PARTITION_KEY = "pk"
SORT_KEY = "sk"
TTL_ATTRIBUTE = "ttl"

SESSION_PREFIX = "SESSION#"
META_SK = "META"

IP_PREFIX = "IP#"
DAY_SK_PREFIX = "DAY#"
MINUTE_SK_PREFIX = "MIN#"


def session_pk(hashed_id: str) -> str:
    """Return the partition key value for a session item, given its hashed id."""
    return f"{SESSION_PREFIX}{hashed_id}"


def ip_pk(hashed_ip: str) -> str:
    """Return the partition key value for an IP rate-limit item, given its hashed IP."""
    return f"{IP_PREFIX}{hashed_ip}"


def session_day_sk(day: date) -> str:
    """Return the sort key for a session's daily question counter (fixed UTC calendar day)."""
    return f"{DAY_SK_PREFIX}{day.isoformat()}"


def ip_minute_sk(minute: datetime) -> str:
    """Return the sort key for an IP's per-minute counter bucket, truncated to the minute."""
    return f"{MINUTE_SK_PREFIX}{minute.strftime('%Y-%m-%dT%H:%M')}"
