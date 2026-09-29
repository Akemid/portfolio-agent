"""CDK app entrypoint (design.md SS3).

Stack instantiation order is `DataStack -> AgentStack -> ApiStack`. This app is
environment-agnostic (no hardcoded account) so `cdk synth --no-lookups` works with
only `CDK_DEFAULT_ACCOUNT`/`CDK_DEFAULT_REGION` set, per `infrastructure` spec
*Infrastructure as Code* — every resource is defined in code, no console steps.

`AGENT_ZIP_PATH` (default `build/agent.zip`, the output of `scripts/build_agent.sh`)
and `LAMBDA_ZIP_PATH` (default `build/lambda.zip`, the output of
`scripts/build_lambda.sh`) let tests point `AgentStack`/`ApiStack`'s assets at small
fixture zips instead of requiring a real build before every synth.

`ALLOWED_ORIGINS` (comma-separated, default `https://sergiomondragon.com` — the same
default `Settings.from_env`, src/api/config.py, uses) is read once here and passed to
both `ApiStack`'s CORS configuration and the Lambda's own `ALLOWED_ORIGINS` env var,
so the two can never drift apart.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# `infra/stacks/data_stack.py` imports the shared key-shape constants from
# `src/api/adapters/dynamo_keys.py` (the single-source-of-truth contract asserted by
# `tests/unit/infra/test_data_stack.py`). Under pytest, `pythonpath = ["src", "."]` in
# `pyproject.toml` puts `src` on the path; the standalone CDK CLI has no such hook, so
# this entrypoint adds it itself before importing anything under `infra.stacks`.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from aws_cdk import App, Environment, Tags  # noqa: E402

from infra.stacks.agent_stack import AgentStack  # noqa: E402
from infra.stacks.api_stack import DOMAIN_NAME, ApiStack  # noqa: E402
from infra.stacks.data_stack import DataStack  # noqa: E402

_DEFAULT_AGENT_ZIP_PATH = Path(__file__).resolve().parent.parent / "build" / "agent.zip"
_DEFAULT_LAMBDA_ZIP_PATH = Path(__file__).resolve().parent.parent / "build" / "lambda.zip"
# Matches `Settings.from_env`'s (src/api/config.py) `_DEFAULT_ALLOWED_ORIGINS`.
_DEFAULT_ALLOWED_ORIGINS = "https://sergiomondragon.com"


def _allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def build_app() -> App:
    app = App()
    env = Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    )

    data_stack = DataStack(app, "portfolio-agent-data", env=env)
    Tags.of(data_stack).add("project", "portfolio-agent")

    agent_stack = AgentStack(
        app,
        "portfolio-agent-agent",
        data=data_stack,
        agent_zip_path=os.environ.get("AGENT_ZIP_PATH", str(_DEFAULT_AGENT_ZIP_PATH)),
        env=env,
    )
    agent_stack.add_stack_dependency(data_stack)
    Tags.of(agent_stack).add("project", "portfolio-agent")

    api_stack = ApiStack(
        app,
        "portfolio-agent-api",
        data=data_stack,
        agent=agent_stack,
        domain_name=DOMAIN_NAME,
        allowed_origins=_allowed_origins(),
        lambda_zip_path=os.environ.get("LAMBDA_ZIP_PATH", str(_DEFAULT_LAMBDA_ZIP_PATH)),
        env=env,
    )
    api_stack.add_stack_dependency(data_stack)
    api_stack.add_stack_dependency(agent_stack)
    Tags.of(api_stack).add("project", "portfolio-agent")

    return app


if __name__ == "__main__":
    build_app().synth()
