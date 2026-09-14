"""Unit tests for `DynamoSessionStore` (design.md SS4.2, `session-identity` spec).

Runs against a moto-mocked DynamoDB table (`dynamodb_table` fixture,
`tests/conftest.py`) — no network calls, no real AWS credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fakes.frozen_clock import FrozenClock

from api.adapters.dynamo_session_store import DynamoSessionStore
from api.domain.hashing import derive_key
from api.domain.models import SessionRecord
from api.domain.session_identity import SESSION_TTL

ISSUED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
RAW_SESSION_ID = "raw-session-id-0000000000000000000000000"


class _FixedIds:
    """An `Ids` double that always returns the same id, for deterministic assertions."""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id

    def new_session_id(self) -> str:
        return self._session_id


def test_get_returns_none_for_unknown_key(dynamodb_table: object) -> None:
    store = DynamoSessionStore(table=dynamodb_table, clock=FrozenClock(ISSUED_AT), ids=_FixedIds(RAW_SESSION_ID))

    assert store.get("some-hash-never-stored") is None


def test_create_writes_ttl_24h_from_issuance(dynamodb_table: object) -> None:
    store = DynamoSessionStore(table=dynamodb_table, clock=FrozenClock(ISSUED_AT), ids=_FixedIds(RAW_SESSION_ID))

    session = store.create()

    hashed_id = derive_key("db", session.session_id)
    item = dynamodb_table.get_item(Key={"pk": f"SESSION#{hashed_id}", "sk": "META"})["Item"]
    expected_ttl = int((ISSUED_AT + SESSION_TTL).timestamp())
    assert item["ttl"] == expected_ttl


def test_create_returns_the_raw_id_for_the_cookie(dynamodb_table: object) -> None:
    store = DynamoSessionStore(table=dynamodb_table, clock=FrozenClock(ISSUED_AT), ids=_FixedIds(RAW_SESSION_ID))

    session = store.create()

    assert session.session_id == RAW_SESSION_ID
    assert session.issued_at == ISSUED_AT


def test_get_round_trips_issued_at_by_hashed_id(dynamodb_table: object) -> None:
    store = DynamoSessionStore(table=dynamodb_table, clock=FrozenClock(ISSUED_AT), ids=_FixedIds(RAW_SESSION_ID))
    created = store.create()

    fetched = store.get(derive_key("db", created.session_id))

    assert fetched == SessionRecord(issued_at=created.issued_at)


def test_get_never_returns_or_stores_the_raw_session_id(dynamodb_table: object) -> None:
    """design.md SS4.2: 'no raw session id ... is ever written'. `get()` returns a
    `SessionRecord`, which has no id field at all — hashed or raw — so the raw cookie
    value cannot leak through it even by accident.
    """
    store = DynamoSessionStore(table=dynamodb_table, clock=FrozenClock(ISSUED_AT), ids=_FixedIds(RAW_SESSION_ID))
    created = store.create()
    hashed_id = derive_key("db", created.session_id)

    fetched = store.get(hashed_id)
    stored_item = dynamodb_table.get_item(Key={"pk": f"SESSION#{hashed_id}", "sk": "META"})["Item"]

    assert fetched is not None
    assert not hasattr(fetched, "session_id")
    assert RAW_SESSION_ID not in stored_item.values()
