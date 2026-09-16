"""The agent's single, read-only Knowledge Base retrieval tool (design.md §5).

**Deviation from design.md's literal text.** Design.md §5 names "the Strands
`retrieve` tool" (from the community `strands-agents-tools` package). Adding
that dependency was measured empirically during this batch (`uv add
strands-agents-tools`) and pulls in ~20 unrelated transitive packages —
`pillow`, `sympy`, `aiohttp`, `slack-bolt`, `beautifulsoup4`,
`markdownify`, `aws-requests-auth`, `dill`, `mpmath`, `prompt-toolkit`, and
more — because that package bundles every community tool (Slack, browser,
memory backends, shell, ...) behind one dependency, not just `retrieve`.
This directly violates design.md §RQ-5's own, more specific dependency-floor
requirement: "`strands-agents`, `bedrock-agentcore`, `boto3`, nothing else.
`pip list` audited in CI" — a floor that exists to keep the AgentCore direct
code deployment zip small and cold start fast. Given the conflict between
these two parts of the same design document, the explicit, testable,
narrower requirement (RQ-5) wins over the illustrative tool-choice sentence
in §5: this module implements the same read-only contract (`agent-runtime`
spec, *No Side-Effect Tools*) as a small `@tool` function directly against
`bedrock-agent-runtime`'s `Retrieve` API, adding zero new dependencies.

API reference (retrieval request/response shape):
https://docs.aws.amazon.com/bedrock/latest/APIReference/API_agent-runtime_Retrieve.html
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError
from strands import tool
from strands.tools.decorator import DecoratedFunctionTool

logger = logging.getLogger(__name__)

_NO_RESULTS_MESSAGE = "No relevant information was found in the portfolio content."
_RETRIEVAL_ERROR_MESSAGE = "The knowledge base is temporarily unavailable."


class RetrieveClient(Protocol):
    """The one `bedrock-agent-runtime` method this tool calls."""

    def retrieve(self, **kwargs: Any) -> dict[str, Any]: ...


def build_search_tool(client: RetrieveClient, knowledge_base_id: str, top_k: int) -> DecoratedFunctionTool[Any, Any]:
    """Build the agent's only tool: read-only semantic search over the
    Knowledge Base (`agent-runtime` spec, *No Side-Effect Tools*).

    `client` and `knowledge_base_id` are injected by the composition root
    (`agent_factory.build_agent`) — this function never constructs a boto3
    client itself, so it is testable with a hand-written double and no
    network access.
    """

    @tool(name="search_portfolio")
    def search_portfolio(query: str) -> str:
        """Search the portfolio owner's CV and portfolio content for passages
        relevant to `query`. Returns the most relevant passages as plain
        text, or a fixed sentence when nothing relevant is found. Never
        raises — a retrieval failure returns a generic message instead."""
        if not query.strip():
            return _NO_RESULTS_MESSAGE

        try:
            response = client.retrieve(
                knowledgeBaseId=knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
            )
        except (ClientError, BotoCoreError):
            logger.exception("knowledge_base_retrieve_failed")
            return _RETRIEVAL_ERROR_MESSAGE

        passages = [
            result["content"]["text"]
            for result in response.get("retrievalResults", [])
            if isinstance(result, dict) and isinstance(result.get("content"), dict) and "text" in result["content"]
        ]
        if not passages:
            return _NO_RESULTS_MESSAGE
        return "\n\n".join(passages)

    return search_portfolio
