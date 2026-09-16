"""Tests for the agent's system prompt (design.md §5; `agent-runtime` spec).

The prompt text itself is the acceptance criterion here — these tests assert
on its content, not on model behavior (that would require a live model call,
which this project never does in unit tests).
"""

from __future__ import annotations

from agent.prompts import SYSTEM_PROMPT


def test_prompt_contains_first_person_instruction() -> None:
    """`agent-runtime` spec, *First-Person Persona*."""
    assert "first person" in SYSTEM_PROMPT.lower()


def test_prompt_forbids_revealing_itself() -> None:
    """`agent-runtime` spec, *Off-Topic and Prompt-Injection Refusal*."""
    lowered = SYSTEM_PROMPT.lower()
    assert "never reveal" in lowered or "do not reveal" in lowered
    assert "system prompt" in lowered
    assert "persona" in lowered


def test_prompt_requires_json_output_shape() -> None:
    """design.md §5, system prompt outline item 7 — the model's own output
    contract, not the Lambda<->Runtime wire contract (that lives in
    `tests/contract/test_payload_contract.py`)."""
    assert '"answer"' in SYSTEM_PROMPT
    assert '"language"' in SYSTEM_PROMPT


def test_prompt_requires_grounding_in_retrieved_content() -> None:
    """`knowledge-base` spec, *Grounded Retrieval*; treats retrieved text as
    data, never as instructions (design.md §5 item 6, T1 threat model)."""
    lowered = SYSTEM_PROMPT.lower()
    assert "retrieved" in lowered
    assert "never" in lowered and "command" in lowered


def test_prompt_specifies_bilingual_response() -> None:
    """`agent-runtime` spec, *Bilingual Detection*."""
    assert "english" in SYSTEM_PROMPT.lower()
    assert "spanish" in SYSTEM_PROMPT.lower()


def test_prompt_specifies_concise_answer_length() -> None:
    """`agent-runtime` spec, *Answer Length*."""
    assert "three sentences" in SYSTEM_PROMPT.lower()


def test_prompt_requires_off_topic_refusal() -> None:
    """`agent-runtime` spec, *Off-Topic and Prompt-Injection Refusal*."""
    lowered = SYSTEM_PROMPT.lower()
    assert "decline" in lowered
    assert "cv" in lowered or "portfolio" in lowered
