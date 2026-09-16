"""AgentCore Runtime entrypoint for the portfolio chat agent (design.md §5).

Uses direct code deployment's `@app.entrypoint` convention, verified against:
https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
https://strandsagents.com/docs/user-guide/deploy/deploy_to_bedrock_agentcore/python/index.md
(payload is a plain dict, handed to the registered function unchanged; the
return value must be JSON-serializable).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent.agent_factory import build_agent
from agent.language import Language, normalize_language

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

_FALLBACK_ANSWER = "Sorry, I could not process that question right now. Please try again shortly."


@app.entrypoint
def agent_invocation(payload: dict[str, Any]) -> dict[str, str]:
    """Handle one invocation. Never raises: every failure path returns the
    same `{"answer", "language"}` shape as success, so the Lambda side never
    sees a traceback (`chat-endpoint` spec, *Upstream Failure Mapping* has no
    special case for a malformed agent response — it is just another
    "answer" string).

    Builds a **fresh** `Agent` per call via `build_agent()` — design.md's D5,
    the single most important line in the agent: a warm, session-affine
    microVM must never accumulate conversation history across a visitor's
    questions (`agent-runtime` spec, *Stateless Single-Turn Answers*).
    """
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return _fallback_response()

    try:
        strands_agent = build_agent()
        raw_output = str(strands_agent(prompt)).strip()
    except Exception:
        logger.exception("agent_invocation_failed")
        return _fallback_response()

    return _parse_model_output(raw_output)


def _parse_model_output(raw_output: str) -> dict[str, str]:
    """Parse the model's `{"answer", "language"}` JSON output (`prompts.py`).

    A model does not always follow instructions exactly, so a non-JSON or
    incomplete response is not treated as a failure: the raw text becomes
    the answer and the language falls back to `normalize_language`'s
    default, rather than discarding a perfectly usable reply.
    """
    try:
        data = json.loads(raw_output)
        answer = data["answer"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("answer must be a non-empty string")
        language: Language = normalize_language(data.get("language"))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {"answer": raw_output or _FALLBACK_ANSWER, "language": normalize_language(None)}

    return {"answer": answer, "language": language}


def _fallback_response() -> dict[str, str]:
    return {"answer": _FALLBACK_ANSWER, "language": normalize_language(None)}


if __name__ == "__main__":
    app.run()
