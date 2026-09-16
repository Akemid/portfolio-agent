"""Tests for `agent.language.normalize_language` (design.md §5, `agent-runtime`
spec *Bilingual Detection*)."""

from __future__ import annotations

import pytest

from agent.language import normalize_language


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("en", "en"),
        ("es", "es"),
        ("EN", "en"),
        ("Es", "es"),
        ("  en  ", "en"),
    ],
)
def test_normalizes_english_and_spanish(raw: str, expected: str) -> None:
    assert normalize_language(raw) == expected


@pytest.mark.parametrize("raw", ["fr", "", "unknown", None, 123, [], {}])
def test_unexpected_value_defaults_to_en(raw: object) -> None:
    assert normalize_language(raw) == "en"
