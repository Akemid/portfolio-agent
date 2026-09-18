"""RED test for task 7.1, extended by task 8.4 (8.4a in PR8a, 8.4b here): the
CDK app skeleton must synthesize with all three stacks wired in dependency
order.

design.md SS3: stack instantiation order DataStack -> AgentStack -> ApiStack.
This batch (PR8b) adds ApiStack and its dependency edges.
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
def _fake_zips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AgentStack`/`ApiStack`'s assets need a real file on disk at synth
    time — point `AGENT_ZIP_PATH`/`LAMBDA_ZIP_PATH` at tiny fixture zips
    instead of the real build outputs."""
    agent_zip_path = tmp_path / "agent.zip"
    with zipfile.ZipFile(agent_zip_path, "w") as zf:
        zf.writestr("main.py", "# fixture only\n")
    monkeypatch.setenv("AGENT_ZIP_PATH", str(agent_zip_path))

    lambda_zip_path = tmp_path / "lambda.zip"
    with zipfile.ZipFile(lambda_zip_path, "w") as zf:
        zf.writestr("api/__init__.py", "")
        zf.writestr("api/handler.py", "def lambda_handler(event, context):\n    return {}\n")
    monkeypatch.setenv("LAMBDA_ZIP_PATH", str(lambda_zip_path))


def test_app_synth_succeeds() -> None:
    app = build_app()
    assembly = app.synth()

    assert {s.stack_name for s in assembly.stacks} == {
        "portfolio-agent-data",
        "portfolio-agent-agent",
        "portfolio-agent-api",
    }

    data_template = Template.from_stack(app.node.find_child("portfolio-agent-data"))
    data_template.resource_count_is("AWS::DynamoDB::Table", 1)

    agent_template = Template.from_stack(app.node.find_child("portfolio-agent-agent"))
    agent_template.resource_count_is("AWS::BedrockAgentCore::Runtime", 1)

    api_template = Template.from_stack(app.node.find_child("portfolio-agent-api"))
    api_template.resource_count_is("AWS::Lambda::Function", 1)
    api_template.resource_count_is("AWS::ApiGatewayV2::Api", 1)


def test_agent_stack_depends_on_data_stack() -> None:
    app = build_app()
    app.synth()

    agent_stack = app.node.find_child("portfolio-agent-agent")
    data_stack = app.node.find_child("portfolio-agent-data")
    assert data_stack in agent_stack.dependencies


def test_api_stack_depends_on_data_and_agent_stacks() -> None:
    app = build_app()
    app.synth()

    api_stack = app.node.find_child("portfolio-agent-api")
    data_stack = app.node.find_child("portfolio-agent-data")
    agent_stack = app.node.find_child("portfolio-agent-agent")
    assert data_stack in api_stack.dependencies
    assert agent_stack in api_stack.dependencies
