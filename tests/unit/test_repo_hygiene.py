"""Repository hygiene checks that guard against committing personal content."""

from pathlib import Path


def test_gitignore_covers_content_and_pdfs(repo_root: Path) -> None:
    """The .gitignore MUST exclude personal content paths and PDF files.

    See `knowledge-base` spec, *Content Never Committed* requirement,
    scenario *Gitignored content path*.
    """
    gitignore_text = (repo_root / ".gitignore").read_text(encoding="utf-8")

    assert "content/" in gitignore_text
    assert "*.pdf" in gitignore_text
