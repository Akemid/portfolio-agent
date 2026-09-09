"""Ports MUST be `typing.Protocol` classes with the signatures from design.md §4."""

import inspect
from typing import Protocol

import pytest

from api.ports.agent_client import AgentClient
from api.ports.clock import Clock
from api.ports.ids import Ids
from api.ports.rate_limiter import RateLimiter
from api.ports.session_store import SessionStore

PORT_METHOD_PARAMS = [
    (SessionStore, "get", ["self", "hashed_id"]),
    (SessionStore, "create", ["self"]),
    (RateLimiter, "check_and_increment", ["self", "session_key", "ip_key"]),
    (AgentClient, "ask", ["self", "prompt", "runtime_session_id"]),
    (Clock, "now", ["self"]),
    (Ids, "new_session_id", ["self"]),
]


@pytest.mark.parametrize("port_cls", [SessionStore, RateLimiter, AgentClient, Clock, Ids])
def test_port_is_a_protocol(port_cls: type) -> None:
    assert issubclass(port_cls, Protocol)
    assert getattr(port_cls, "_is_protocol", False) is True


@pytest.mark.parametrize(("port_cls", "method_name", "params"), PORT_METHOD_PARAMS)
def test_port_method_has_expected_parameters(port_cls: type, method_name: str, params: list[str]) -> None:
    method = getattr(port_cls, method_name)
    assert list(inspect.signature(method).parameters) == params
