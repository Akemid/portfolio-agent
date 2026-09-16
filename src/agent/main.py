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
import os
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent.agent_factory import build_agent
from agent.language import Language, normalize_language
from agent.settings import max_answer_chars_from_env

logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

_FALLBACK_ANSWER = "Sorry, I could not process that question right now. Please try again shortly."
_SENTENCE_ENDINGS = (".", "!", "?")


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

    return _parse_model_output(raw_output, max_answer_chars_from_env(os.environ))


def _parse_model_output(raw_output: str, max_answer_chars: int) -> dict[str, str]:
    """Parse the model's `{"answer", "language"}` JSON output (`prompts.py`).

    A model does not always follow instructions exactly, so a non-JSON or
    incomplete response is not treated as a failure: the raw text becomes
    the answer and the language falls back to `normalize_language`'s
    default, rather than discarding a perfectly usable reply.

    `max_answer_chars` hard-caps whatever ends up as `answer` on every path
    (MAJOR/MEDIUM finding: an unbounded answer is a cost/availability risk
    regardless of which branch produced it).
    """
    try:
        data = json.loads(raw_output)
        answer = data["answer"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("answer must be a non-empty string")
        language: Language = normalize_language(data.get("language"))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        fallback_text = raw_output or _FALLBACK_ANSWER
        return {"answer": _truncate_answer(fallback_text, max_answer_chars), "language": normalize_language(None)}

    return {"answer": _truncate_answer(answer, max_answer_chars), "language": language}


def _truncate_answer(answer: str, max_chars: int) -> str:
    """Hard-cap `answer` to `max_chars`, cutting at a sentence boundary when
    one exists in range, else at a word boundary, so a truncated answer
    never ends mid-word. Falls back to a hard cut only for a single token
    longer than `max_chars`."""
    if len(answer) <= max_chars:
        return answer

    window = answer[:max_chars]
    sentence_end = max(window.rfind(ending) for ending in _SENTENCE_ENDINGS)
    if sentence_end != -1:
        return window[: sentence_end + 1]

    word_boundary = window.rfind(" ")
    if word_boundary != -1:
        return window[:word_boundary].rstrip()

    return window


def _fallback_response() -> dict[str, str]:
    return {"answer": _FALLBACK_ANSWER, "language": normalize_language(None)}


if __name__ == "__main__":
    app.run()
