"""RED test for task 7.1, extended by task 8.4 (PR8a slice): the CDK app skeleton
must synthesize with DataStack and AgentStack wired in dependency order.

design.md SS3: stack instantiation order DataStack -> AgentStack -> ApiStack.
ApiStack lands in PR8b; this batch wires DataStack and AgentStack.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest
from aws_cdk.assertions import Template

os.environ.setdefault("CDK_DEFAULT_ACCOUNT", "000000000000")
os.environ.setdefault("CDK_DEFAULT_REGION", "us-east-1")

from infra.app import build_app  # noqa: E402  (env vars must be set first)


@pytest.fixture(autouse=True)
def _fake_agent_zip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AgentStack`'s asset needs a real file on disk at synth time — point
    `AGENT_ZIP_PATH` at a tiny fixture zip instead of the real build output."""
    zip_path = tmp_path / "agent.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("main.py", "# fixture only\n")
    monkeypatch.setenv("AGENT_ZIP_PATH", str(zip_path))


def test_app_synth_succeeds() -> None:
    app = build_app()
    assembly = app.synth()

    assert {s.stack_name for s in assembly.stacks} == {"portfolio-agent-data", "portfolio-agent-agent"}

    data_template = Template.from_stack(app.node.find_child("portfolio-agent-data"))
    data_template.resource_count_is("AWS::DynamoDB::Table", 1)

    agent_template = Template.from_stack(app.node.find_child("portfolio-agent-agent"))
    agent_template.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)


def test_agent_stack_depends_on_data_stack() -> None:
    app = build_app()
    app.synth()

    agent_stack = app.node.find_child("portfolio-agent-agent")
    data_stack = app.node.find_child("portfolio-agent-data")
    assert data_stack in agent_stack.dependencies
