"""Builds the Strands `Agent` for the portfolio chat runtime (design.md §5).

Docs verified:
- `Agent`/`BedrockModel` construction: https://strandsagents.com/docs/user-guide/quickstart/python/
- `@tool` decorator: https://strandsagents.com/docs/user-guide/concepts/tools/index.md
- `BedrockModel(max_tokens=..., temperature=...)` output-bounding params:
  https://github.com/strands-agents/docs/blob/main/site/src/content/docs/user-guide/deploy/operating-agents-in-production.mdx
  (via Context7 `/strands-agents/docs`, 2026-09-15)
"""

from __future__ import annotations

import os

import boto3
from strands import Agent
from strands.models import BedrockModel

from agent.knowledge_base import build_search_tool
from agent.prompts import SYSTEM_PROMPT
from agent.settings import AgentSettings


def build_agent() -> Agent:
    """Build a fresh `Agent` for one invocation.

    Called **inside** the AgentCore entrypoint, never at module scope
    (design.md D5) — a module-scope `Agent` on a warm, session-affine
    microVM would silently accumulate conversation history across a
    visitor's questions, breaking the *Stateless Single-Turn Answers*
    requirement (`agent-runtime` spec).

    The model id, Knowledge Base id, region, and result count are all read
    from the environment at call time (`AgentSettings.from_env`), never
    hardcoded, so any of them can change without a code change
    (`agent-runtime` spec, *Foundation Model*).
    """
    settings = AgentSettings.from_env(os.environ)
    model = BedrockModel(
        model_id=settings.model_id,
        region_name=settings.aws_region,
        max_tokens=settings.max_tokens,
        temperature=settings.temperature,
    )
    retrieve_client = boto3.client("bedrock-agent-runtime", region_name=settings.aws_region)
    search_tool = build_search_tool(
        retrieve_client,
        knowledge_base_id=settings.knowledge_base_id,
        top_k=settings.retrieval_top_k,
    )
    return Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=[search_tool])
