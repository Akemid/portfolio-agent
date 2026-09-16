"""Cross-phase contract test (task 5.3, unskipped by task 6.5): one JSON
fixture pair enforced from BOTH sides of the Lambda <-> AgentCore Runtime
boundary (design.md §6; `agent-runtime` spec, *Invocation Payload Contract*).
A one-sided change to either `AgentCoreClient` (the Lambda side) or
`agent.main.agent_invocation` (the agent side) fails this test.

No network: the boto3 client and the Strands agent are both fakes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import agent.main as agent_main
from api.adapters.agentcore_client import AgentCoreClient
from api.domain.models import AgentAnswer

_REQUEST_FIXTURE = {"prompt": "What did you build?", "language_hint": None}
_RESPONSE_FIXTURE = {"answer": "I built a portfolio chatbot.", "language": "en"}
_ARN = "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/test-agent"
_RUNTIME_SESSION_ID = "a" * 64


class _FakeStreamingBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _RecordingBotoClient:
    """Records the request the Lambda side sends and returns a scripted,
    AgentCore-shaped response for the agent side's answer."""

    def __init__(self, response_body: dict[str, Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response_body = response_body

    def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "contentType": "application/json",
            "response": _FakeStreamingBody(json.dumps(self._response_body).encode()),
        }


class _FakeStrandsAgent:
    """Stands in for a Strands `Agent`: `str(agent(prompt))` returns fixed text."""

    def __init__(self, raw_text: str) -> None:
        self._raw_text = raw_text

    def __call__(self, prompt: str) -> str:
        return self._raw_text


def test_lambda_produced_request_payload_matches_the_fixture_shape() -> None:
    """The Lambda's `AgentCoreClient.ask()` sends exactly the fixture's
    request shape (`{"prompt", "language_hint"}`)."""
    client = _RecordingBotoClient(response_body=_RESPONSE_FIXTURE)
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    adapter.ask(_REQUEST_FIXTURE["prompt"], runtime_session_id=_RUNTIME_SESSION_ID)

    sent_payload = json.loads(client.calls[0]["payload"])
    assert sent_payload == _REQUEST_FIXTURE


def test_agent_entrypoint_accepts_the_fixture_request_and_the_response_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent entrypoint accepts the exact fixture request payload, and
    the response it produces round-trips through the Lambda's own response
    parser (`AgentCoreClient._parse_answer`) -- one JSON fixture pair, both
    directions of the contract."""
    monkeypatch.setattr(agent_main, "build_agent", lambda: _FakeStrandsAgent(json.dumps(_RESPONSE_FIXTURE)))

    result = agent_main.agent_invocation(dict(_REQUEST_FIXTURE))

    assert result == _RESPONSE_FIXTURE

    client = _RecordingBotoClient(response_body=result)
    adapter = AgentCoreClient(client, agent_runtime_arn=_ARN)

    answer = adapter.ask("irrelevant for this direction of the test", runtime_session_id=_RUNTIME_SESSION_ID)

    assert answer == AgentAnswer(answer=_RESPONSE_FIXTURE["answer"], language=_RESPONSE_FIXTURE["language"])
