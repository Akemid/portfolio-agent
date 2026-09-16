"""CDK app entrypoint (design.md SS3).

Stack instantiation order is `DataStack -> AgentStack -> ApiStack`; ApiStack is
added in PR8b. This app is environment-agnostic (no hardcoded account) so
`cdk synth --no-lookups` works with only `CDK_DEFAULT_ACCOUNT`/`CDK_DEFAULT_REGION`
set, per `infrastructure` spec *Infrastructure as Code* — every resource is defined
in code, no console steps.

`AGENT_ZIP_PATH` (default `build/agent.zip`, the output of `scripts/build_agent.sh`)
lets tests point `AgentStack`'s asset at a small fixture zip instead of requiring a
real build before every synth.
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
from infra.stacks.data_stack import DataStack  # noqa: E402

_DEFAULT_AGENT_ZIP_PATH = Path(__file__).resolve().parent.parent / "build" / "agent.zip"


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

    return app


if __name__ == "__main__":
    build_app().synth()
