"""Tests for `agent.knowledge_base.build_search_tool` (design.md §5; §8 RQ-2
"prefer a small own tool" deviation — see module docstring for why this
project does not depend on `strands-agents-tools`' built-in `retrieve` tool).

No network: `client` is always a hand-written double.
"""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from agent.knowledge_base import build_search_tool


class _FakeRetrieveClient:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def retrieve(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _result(text: str, score: float = 0.9) -> dict[str, Any]:
    return {"content": {"text": text}, "score": score}


def test_sends_query_text_kb_id_and_top_k() -> None:
    client = _FakeRetrieveClient(response={"retrievalResults": [_result("I built X.")]})
    search = build_search_tool(client, knowledge_base_id="KB123", top_k=4)

    search("what did you build?")

    call = client.calls[0]
    assert call["knowledgeBaseId"] == "KB123"
    assert call["retrievalQuery"] == {"text": "what did you build?"}
    assert call["retrievalConfiguration"] == {"vectorSearchConfiguration": {"numberOfResults": 4}}


def test_formats_passages_from_multiple_results() -> None:
    client = _FakeRetrieveClient(response={"retrievalResults": [_result("Passage one."), _result("Passage two.")]})
    search = build_search_tool(client, knowledge_base_id="KB123", top_k=4)

    result = search("anything")

    assert "Passage one." in result
    assert "Passage two." in result


def test_empty_results_returns_a_fixed_no_information_sentence() -> None:
    client = _FakeRetrieveClient(response={"retrievalResults": []})
    search = build_search_tool(client, knowledge_base_id="KB123", top_k=4)

    result = search("something totally unrelated")

    assert "no relevant information" in result.lower()


def test_client_error_never_raises_and_returns_a_generic_message() -> None:
    error = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Retrieve")
    client = _FakeRetrieveClient(error=error)
    search = build_search_tool(client, knowledge_base_id="KB123", top_k=4)

    result = search("anything")

    assert "slow down" not in result
    assert "ThrottlingException" not in result


def test_malformed_result_entries_are_skipped_not_raised() -> None:
    client = _FakeRetrieveClient(response={"retrievalResults": [{"score": 0.5}, _result("The only usable passage.")]})
    search = build_search_tool(client, knowledge_base_id="KB123", top_k=4)

    result = search("anything")

    assert result == "The only usable passage."


def test_tool_name_is_search_portfolio() -> None:
    client = _FakeRetrieveClient(response={"retrievalResults": []})
    search = build_search_tool(client, knowledge_base_id="KB123", top_k=4)

    assert search.tool_name == "search_portfolio"


@pytest.mark.parametrize("bad_query", ["", "   "])
def test_blank_query_short_circuits_without_calling_the_client(bad_query: str) -> None:
    client = _FakeRetrieveClient(response={"retrievalResults": []})
    search = build_search_tool(client, knowledge_base_id="KB123", top_k=4)

    result = search(bad_query)

    assert client.calls == []
    assert "no relevant information" in result.lower()
