"""CI workflow structure checks.

See `infrastructure` spec, requirement *CI Quality Gates*.
"""

from pathlib import Path
from typing import Any

import yaml

REQUIRED_COMMANDS = [
    "ruff check",
    "ruff format --check",
    "pytest",
    "--cov-fail-under=85",
    "gitleaks",
    "pip-audit",
    "check_no_content.sh",
    "cdk synth",
    "aws-cdk@2",
    "--no-lookups",
]

# The cdk synth step MUST run against a neutralized, non-real AWS account —
# never real credentials or a real account id (Phase 9 task 9.4/CI change:
# gotchas/cdk-asset-account-leak).
REQUIRED_CDK_SYNTH_SAFETY_ENV_VARS = [
    "CDK_DEFAULT_ACCOUNT",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
]


def _load_workflow(repo_root: Path) -> dict[str, Any]:
    workflow_path = repo_root / ".github" / "workflows" / "ci.yml"
    loaded: dict[str, Any] = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    return loaded


def _flatten_run_commands(workflow: dict[str, Any]) -> str:
    """Concatenate every `run:` step string in the workflow for substring checks."""
    chunks: list[str] = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "run" in step:
                chunks.append(step["run"])
    return "\n".join(chunks)


def test_ci_yaml_has_required_jobs(repo_root: Path) -> None:
    """The CI workflow MUST run every blocking quality gate command."""
    workflow = _load_workflow(repo_root)

    assert "on" in workflow or True in workflow  # PyYAML parses bare `on:` as True
    assert "jobs" in workflow
    assert len(workflow["jobs"]) >= 1

    all_run_steps = _flatten_run_commands(workflow)
    for command in REQUIRED_COMMANDS:
        assert command in all_run_steps, f"missing required CI command: {command}"


def _find_cdk_synth_step(workflow: dict[str, Any]) -> dict[str, Any]:
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            if "aws-cdk@2 synth" in step.get("run", ""):
                return step
    raise AssertionError("no CI step runs `npx aws-cdk@2 synth`")


def test_cdk_synth_step_never_uses_a_real_aws_account(repo_root: Path) -> None:
    """The cdk synth step MUST run with a neutralized fake account, never
    real AWS credentials — synth must work identically on a fork PR with no
    AWS access, and must never leak a real account id into a log."""
    workflow = _load_workflow(repo_root)
    step = _find_cdk_synth_step(workflow)
    env = step.get("env", {})

    for var in REQUIRED_CDK_SYNTH_SAFETY_ENV_VARS:
        assert var in env, f"cdk synth step must set {var} (see gotchas/cdk-asset-account-leak)"
    assert env["CDK_DEFAULT_ACCOUNT"] == "000000000000", "must use the CDK-documented fake account"
    assert "--no-lookups" in step["run"]


def test_ci_triggers_on_push_and_pull_request(repo_root: Path) -> None:
    """The CI workflow MUST trigger on both push and pull_request events."""
    workflow = _load_workflow(repo_root)
    # PyYAML (YAML 1.1) parses a bare `on:` key as the boolean True, not the
    # string "on" — this is why we also probe workflow[True].
    triggers: dict[str, Any] = workflow.get("on") or workflow.get(True) or {}  # type: ignore[call-overload]

    assert "push" in triggers
    assert "pull_request" in triggers
