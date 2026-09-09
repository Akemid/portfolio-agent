"""Tests for the CI content-guard check.

See `knowledge-base` spec, *Content Never Committed* requirement,
scenario *CI content check*.
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_no_content.sh"


def _run_guard(diff_text: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    diff_file = tmp_path / "diff.patch"
    diff_file.write_text(diff_text, encoding="utf-8")
    return subprocess.run(
        ["bash", str(SCRIPT), str(diff_file)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_content_guard_fails_on_content_diff(tmp_path: Path) -> None:
    """A diff that adds a path under `content/` MUST fail the guard."""
    diff_text = (
        "diff --git a/content/cv/x.pdf b/content/cv/x.pdf\n"
        "new file mode 100644\n"
        "index 0000000..1234567\n"
        "--- /dev/null\n"
        "+++ b/content/cv/x.pdf\n"
    )

    result = _run_guard(diff_text, tmp_path)

    assert result.returncode != 0


def test_content_guard_fails_on_pdf_outside_content(tmp_path: Path) -> None:
    """A diff that adds a PDF anywhere, in any letter case, MUST fail the guard."""
    diff_text = (
        "diff --git a/docs/Resume.PDF b/docs/Resume.PDF\n"
        "new file mode 100644\n"
        "index 0000000..1234567\n"
        "--- /dev/null\n"
        "+++ b/docs/Resume.PDF\n"
    )

    result = _run_guard(diff_text, tmp_path)

    assert result.returncode != 0
    assert "PDF" in result.stderr


def test_content_guard_fails_on_nested_content_dir(tmp_path: Path) -> None:
    """A `content/` directory nested below the repo root MUST also fail the guard."""
    diff_text = (
        "diff --git a/data/content/es/about.md b/data/content/es/about.md\n"
        "new file mode 100644\n"
        "index 0000000..1234567\n"
        "--- /dev/null\n"
        "+++ b/data/content/es/about.md\n"
    )

    result = _run_guard(diff_text, tmp_path)

    assert result.returncode != 0


def test_content_guard_passes_on_clean_diff(tmp_path: Path) -> None:
    """A diff that does not touch `content/` MUST pass the guard."""
    diff_text = (
        "diff --git a/src/api/domain/models.py b/src/api/domain/models.py\n"
        "new file mode 100644\n"
        "index 0000000..1234567\n"
        "--- /dev/null\n"
        "+++ b/src/api/domain/models.py\n"
    )

    result = _run_guard(diff_text, tmp_path)

    assert result.returncode == 0
