"""RED test for the Lambda deployment package build script (task 8.3 prep;
design.md SS4, SS9.2 packaging flow), mirroring
`tests/unit/scripts/test_build_agent_layout.py`'s pattern for
`scripts/build_agent.sh`.

`src/api` is a plain package (no `main.py` shim needed the way the AgentCore
direct-deploy entrypoint required one): `infra/stacks/api_stack.py` wires
`aws_lambda.Function(handler="api.handler.lambda_handler", ...)`, a dotted
path Lambda resolves through the `api` package directly, so the build only
needs `api/` preserved at the zip root plus the Lambda's own runtime
dependencies (boto3, per `pyproject.toml`'s base `[project.dependencies]` —
never the `agent`/`infra`/`dev` groups, enforced by the existing
`test_lambda_package_isolation.py` import guard).

Exercises the real `scripts/build_lambda.sh` copy step in a tmp dir via
`BUILD_SKIP_DEPS=1` (skips the network-dependent `uv export`/`uv pip install`
steps) and a build-dir override argument, so this test never installs
anything or requires network access.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_lambda.sh"


def _run_build(build_dir: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BUILD_SKIP_DEPS": "1", **(extra_env or {})}
    return subprocess.run(
        ["bash", str(BUILD_SCRIPT), str(build_dir)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_build_layout_preserves_the_api_package(tmp_path: Path) -> None:
    build_dir = tmp_path / "lambda"

    result = _run_build(build_dir)

    assert result.returncode == 0, result.stderr
    assert (build_dir / "api" / "handler.py").is_file()
    assert (build_dir / "api" / "__init__.py").is_file()


def test_build_produces_a_zip_with_the_handler_importable_at_the_dotted_path(tmp_path: Path) -> None:
    build_dir = tmp_path / "lambda"
    build_result = _run_build(build_dir)
    assert build_result.returncode == 0, build_result.stderr

    zip_path = build_dir.parent / "lambda.zip"
    assert zip_path.is_file()

    import_env = {**os.environ, "PYTHONPATH": str(build_dir)}
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT),
            "python",
            "-c",
            "from api.handler import lambda_handler; assert callable(lambda_handler)",
        ],
        cwd=build_dir,
        env=import_env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr


def test_build_fails_when_zip_exceeds_the_size_limit(tmp_path: Path) -> None:
    """Same guard as `build_agent.sh`: exceeding the configured zip size limit
    must fail the build (`exit 1`), not just warn."""
    build_dir = tmp_path / "lambda"

    result = _run_build(build_dir, {"BUILD_MAX_ZIP_BYTES": "1"})

    assert result.returncode == 1
    assert "250 MB" in result.stderr
