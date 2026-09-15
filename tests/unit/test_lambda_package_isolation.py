"""Guards the Lambda deployment package against heavy, non-runtime dependencies.

`src/api` is what ships in the Lambda zip (design.md §4). `aws_cdk`/`constructs`
belong to the CDK app (Phase 7-8) and `strands`/`bedrock_agentcore` (the SDK
package, not the boto3 `bedrock-agentcore` client) belong to the agent runtime
(Phase 6) — neither should ever be imported from `src/api`, or the Lambda zip
would bundle megabytes of unused code and slow every cold start.
"""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_TOP_LEVEL_MODULES = ("aws_cdk", "constructs", "strands", "bedrock_agentcore")


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def test_api_package_never_imports_cdk_or_agent_dependencies(repo_root: Path) -> None:
    api_dir = repo_root / "src" / "api"
    offenders: dict[str, set[str]] = {}
    for path in api_dir.rglob("*.py"):
        found = _imported_top_level_modules(path) & set(_FORBIDDEN_TOP_LEVEL_MODULES)
        if found:
            offenders[str(path.relative_to(repo_root))] = found

    assert offenders == {}
