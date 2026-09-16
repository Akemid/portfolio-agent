"""Normalizes a raw language value to the two languages this agent supports
(design.md §5; `agent-runtime` spec, *Bilingual Detection*).

The model is instructed to emit `"en"` or `"es"` directly (`prompts.py`), but
its output is still untrusted text — this is the single place that turns
whatever the model actually said into one of exactly two values, defaulting
to `"en"` rather than raising, since a language field the agent got wrong is
never worth failing the whole answer over.
"""

from __future__ import annotations

from typing import Literal

Language = Literal["en", "es"]

_DEFAULT_LANGUAGE: Language = "en"
_SUPPORTED: frozenset[str] = frozenset({"en", "es"})


def normalize_language(raw: object) -> Language:
    """Return `"en"` or `"es"`, defaulting to `"en"` for anything else."""
    if isinstance(raw, str):
        candidate = raw.strip().lower()
        if candidate in _SUPPORTED:
            return candidate  # type: ignore[return-value]
    return _DEFAULT_LANGUAGE
