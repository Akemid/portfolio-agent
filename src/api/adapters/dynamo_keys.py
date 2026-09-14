"""Single source of truth for the DynamoDB session item key shape (design.md SS4.2).

Both the real adapter (`dynamo_session_store.py`) and the test schema helper
(`tests/helpers/dynamodb_schema.py`) import the attribute names, prefix, and
key-builder from here so the key shape can never drift between production
code and test setup. `tests/` MAY import from `src/`; `src/` MUST NEVER
import from `tests/`.
"""

from __future__ import annotations

PARTITION_KEY = "pk"
SORT_KEY = "sk"
TTL_ATTRIBUTE = "ttl"

SESSION_PREFIX = "SESSION#"
META_SK = "META"


def session_pk(hashed_id: str) -> str:
    """Return the partition key value for a session item, given its hashed id."""
    return f"{SESSION_PREFIX}{hashed_id}"
