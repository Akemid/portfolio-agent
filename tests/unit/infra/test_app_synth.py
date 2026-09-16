"""RED test for task 7.1: the CDK app skeleton must synthesize.

design.md SS3: stack instantiation order DataStack -> AgentStack -> ApiStack
(Agent/Api stacks land in Phase 8). This batch only wires DataStack, so
synth is expected to produce exactly one stack template.
"""

from __future__ import annotations

import os

from aws_cdk.assertions import Template

os.environ.setdefault("CDK_DEFAULT_ACCOUNT", "000000000000")
os.environ.setdefault("CDK_DEFAULT_REGION", "us-east-1")

from infra.app import build_app  # noqa: E402  (env vars must be set first)


def test_app_synth_succeeds() -> None:
    app = build_app()
    assembly = app.synth()

    assert [s.stack_name for s in assembly.stacks] == ["portfolio-agent-data"]

    template = Template.from_stack(app.node.find_child("portfolio-agent-data"))
    template.resource_count_is("AWS::DynamoDB::Table", 1)
