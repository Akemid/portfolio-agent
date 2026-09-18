"""Literal names shared between `infra/` (CDK) and `scripts/` (operator
tooling), defined exactly once so the two subsystems can never silently
drift apart on a stack name or the Bedrock data source name.

Deliberately placed under `src/shared/` rather than `src/api/` or
`infra/names.py`:

- `scripts/build_lambda.sh` copies only `src/api/` into the Lambda zip
  (`cp -R src/api/. build/lambda/api/`), so a module here is never bundled
  into the Lambda deployment package.
- `tests/unit/test_lambda_package_isolation.py` only forbids `src/api` from
  importing `aws_cdk`/`constructs`/`strands`/`bedrock_agentcore`, and
  `src/agent` from importing `api` — a new `src/shared` package touches
  neither rule, and this module itself imports nothing (no CDK, no boto3),
  so importing it from either side is always safe.
- `infra/app.py` already inserts `src/` onto `sys.path` before importing
  `infra.stacks.*`, and `pyproject.toml`'s `pythonpath = ["src", "scripts",
  "."]` does the same for tests and `scripts/sync_content.py` — so
  `from shared.names import ...` resolves identically in both the CDK app
  and the operator script with no extra wiring.
"""

from __future__ import annotations

DATA_STACK_NAME = "portfolio-agent-data"
AGENT_STACK_NAME = "portfolio-agent-agent"
API_STACK_NAME = "portfolio-agent-api"
# Matches `infra/stacks/data_stack.py`'s `CfnDataSource(name=...)`.
CONTENT_DATA_SOURCE_NAME = "portfolio-agent-content"
