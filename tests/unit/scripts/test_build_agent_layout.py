"""RED test for the BLOCKER zip-layout bug (design.md SS3, SS8 RQ-2 packaging
flow): `src/agent/*.py` import siblings as `from agent.x import y`, so the zip
root must contain an `agent/` package directory plus a root `main.py` shim
that does `from agent.main import app` — never `src/agent`'s files dumped
flat at the zip root (that leaves `agent` unimportable).

Exercises the real `scripts/build_agent.sh` copy step in a tmp dir via
`BUILD_SKIP_DEPS=1` (skips the network-dependent `uv export`/`uv pip install`
steps) and a build-dir override argument, then imports the produced root
`main.py` in a subprocess with `PYTHONPATH` pointed at that tmp dir. The
subprocess runs through `uv run --project <repo>` so it inherits the `agent`
dependency group (`strands`, `bedrock_agentcore`) already installed in this
repo's dev environment, without installing anything into the tmp dir itself.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_agent.sh"


def _run_build(build_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "BUILD_SKIP_DEPS": "1"}
    return subprocess.run(
        ["bash", str(BUILD_SCRIPT), str(build_dir)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_build_layout_preserves_the_agent_package(tmp_path: Path) -> None:
    build_dir = tmp_path / "agent"

    result = _run_build(build_dir)

    assert result.returncode == 0, result.stderr
    assert (build_dir / "agent" / "agent_factory.py").is_file()
    assert (build_dir / "agent" / "main.py").is_file()


def test_build_layout_root_main_is_importable_and_exposes_app(tmp_path: Path) -> None:
    build_dir = tmp_path / "agent"
    build_result = _run_build(build_dir)
    assert build_result.returncode == 0, build_result.stderr

    import_env = {**os.environ, "PYTHONPATH": str(build_dir)}
    proc = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(REPO_ROOT),
            "python",
            "-c",
            "import main; assert hasattr(main, 'app'), 'main.app missing'",
        ],
        cwd=build_dir,
        env=import_env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
