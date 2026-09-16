"""Guards the Lambda deployment package against heavy, non-runtime dependencies,
and guards the agent package against pulling in the Lambda gate's code.

`src/api` is what ships in the Lambda zip (design.md §4). `aws_cdk`/`constructs`
belong to the CDK app (Phase 7-8) and `strands`/`bedrock_agentcore` (the SDK
package, not the boto3 `bedrock-agentcore` client) belong to the agent runtime
(Phase 6) — neither should ever be imported from `src/api`, or the Lambda zip
would bundle megabytes of unused code and slow every cold start. Conversely
`src/agent` (design.md §5) is packaged and deployed separately by
`scripts/build_agent.sh` (Phase 8) and must never import `src/api` — the two
are independent deployables with no shared runtime code (design.md §1,
*Split of responsibility*).
"""

from __future__ import annotations

import ast
from pathlib import Path

_FORBIDDEN_IN_API = ("aws_cdk", "constructs", "strands", "bedrock_agentcore")
_FORBIDDEN_IN_AGENT = ("api",)


def _imported_top_level_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


def _offenders(package_dir: Path, repo_root: Path, forbidden: tuple[str, ...]) -> dict[str, set[str]]:
    offenders: dict[str, set[str]] = {}
    for path in package_dir.rglob("*.py"):
        found = _imported_top_level_modules(path) & set(forbidden)
        if found:
            offenders[str(path.relative_to(repo_root))] = found
    return offenders


def test_api_package_never_imports_cdk_or_agent_dependencies(repo_root: Path) -> None:
    assert _offenders(repo_root / "src" / "api", repo_root, _FORBIDDEN_IN_API) == {}


def test_agent_package_never_imports_the_api_package(repo_root: Path) -> None:
    assert _offenders(repo_root / "src" / "agent", repo_root, _FORBIDDEN_IN_AGENT) == {}
