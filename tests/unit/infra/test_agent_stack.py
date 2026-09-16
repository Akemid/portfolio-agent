"""RED tests for task 8.2: the CDK AgentStack (design.md SS3, SS5, SS8 RQ-1/RQ-2,
SS9.2 — PR8a boundary: agent code asset, `CfnRuntime`, agent execution role, outputs).

Verified against AWS documentation before writing `infra/stacks/agent_stack.py` (see
its module docstring for the cited URLs): `AWS::BedrockAgentCore::Runtime`'s
`CodeConfiguration`/`NetworkConfiguration`/`ProtocolConfiguration` shapes, the
direct-deploy execution role's required CloudWatch Logs actions, and the
`infrastructure` spec's *Least-Privilege Agent Role* requirement (exactly one KB ARN,
the exact model ARNs, no wildcard resource).
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from infra.stacks.agent_stack import (
    AGENT_RUNTIME_NAME,
    ENTRY_POINT,
    NOVA_MICRO_INFERENCE_PROFILE_ID,
    NOVA_MICRO_MODEL_ID,
    AgentStack,
)
from infra.stacks.data_stack import DataStack

_LEAST_PRIVILEGE_SIDS = {"AnswerModelOnly", "RetrieveOneKb", "ReadOwnCode"}


@pytest.fixture
def fake_agent_zip(tmp_path: Path) -> str:
    """A tiny real zip so `aws_s3_assets.Asset` can hash/read it at synth time."""
    zip_path = tmp_path / "agent.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("main.py", "# fixture only\n")
    return str(zip_path)


def _build_stacks(agent_zip_path: str) -> tuple[DataStack, AgentStack]:
    app = App()
    env = Environment(account="000000000000", region="us-east-1")
    data_stack = DataStack(app, "TestDataStack", env=env)
    agent_stack = AgentStack(app, "TestAgentStack", data=data_stack, agent_zip_path=agent_zip_path, env=env)
    return data_stack, agent_stack


def _synth_template(agent_zip_path: str) -> Template:
    _, agent_stack = _build_stacks(agent_zip_path)
    return Template.from_stack(agent_stack)


def _all_policy_statements(template_json: dict[str, Any]) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    for resource in template_json["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        statements.extend(resource["Properties"]["PolicyDocument"]["Statement"])
    return statements


def test_runtime_uses_code_configuration_not_container(fake_agent_zip: str) -> None:
    template = _synth_template(fake_agent_zip)

    template.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        {
            "AgentRuntimeArtifact": {
                "CodeConfiguration": Match.object_like(
                    {
                        "Runtime": "PYTHON_3_12",
                        "EntryPoint": ENTRY_POINT,
                    }
                )
            },
            "NetworkConfiguration": {"NetworkMode": "PUBLIC"},
            "ProtocolConfiguration": "HTTP",
        },
    )
    (resource,) = template.find_resources("AWS::BedrockAgentCore::Runtime").values()
    assert "ContainerConfiguration" not in resource["Properties"]["AgentRuntimeArtifact"]


def test_agent_runtime_name_matches_the_cfn_allowed_pattern(fake_agent_zip: str) -> None:
    """`AgentRuntimeName` must match `^[a-zA-Z][a-zA-Z0-9_]{0,47}$` — no hyphens."""
    template = _synth_template(fake_agent_zip)

    template.has_resource_properties("AWS::BedrockAgentCore::Runtime", {"AgentRuntimeName": AGENT_RUNTIME_NAME})
    assert re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", AGENT_RUNTIME_NAME)


def test_agent_role_grants_both_model_arns(fake_agent_zip: str) -> None:
    template = _synth_template(fake_agent_zip)
    template_json = template.to_json()

    statement = next(s for s in _all_policy_statements(template_json) if s.get("Sid") == "AnswerModelOnly")
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "bedrock:InvokeModel"
    resources = statement["Resource"]
    assert isinstance(resources, list)
    assert len(resources) == 2
    # Both ARNs are built from the `AWS::Region`/`AWS::AccountId` pseudo-parameters
    # (Aws.REGION / Aws.ACCOUNT_ID), so they synthesize as Fn::Join tokens, not
    # literal strings — assert the literal ARN suffixes are present in the joins.
    assert f"::foundation-model/{NOVA_MICRO_MODEL_ID}" in resources[0]["Fn::Join"][1]
    assert f":inference-profile/{NOVA_MICRO_INFERENCE_PROFILE_ID}" in resources[1]["Fn::Join"][1]


def test_agent_role_retrieves_only_the_one_knowledge_base(fake_agent_zip: str) -> None:
    template = _synth_template(fake_agent_zip)
    template_json = template.to_json()

    statement = next(s for s in _all_policy_statements(template_json) if s.get("Sid") == "RetrieveOneKb")
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "bedrock:Retrieve"
    resource = statement["Resource"]
    # Cross-stack reference to DataStack's KnowledgeBase ARN: CDK exports it from
    # DataStack and imports it here — one specific ARN, no wildcard.
    assert isinstance(resource, dict)
    assert "Fn::ImportValue" in resource
    assert "KnowledgeBaseArn" in resource["Fn::ImportValue"]


def test_agent_role_reads_only_its_own_code_object(fake_agent_zip: str) -> None:
    template = _synth_template(fake_agent_zip)
    template_json = template.to_json()

    statement = next(s for s in _all_policy_statements(template_json) if s.get("Sid") == "ReadOwnCode")
    assert statement["Effect"] == "Allow"
    assert statement["Action"] == "s3:GetObject"
    # One specific object ARN (bucket + key), never a bucket-wide or content/* prefix.
    resource = statement["Resource"]
    assert isinstance(resource, dict)
    assert "Fn::Join" in resource


def test_agent_role_has_no_wildcard_resource(fake_agent_zip: str) -> None:
    """Least-Privilege Agent Role: the Bedrock/S3 statements must reference exactly
    one ARN each, with no wildcard character anywhere in that ARN — the runtime's own
    CloudWatch Logs statements (a documented AWS requirement, not covered by this
    spec requirement) are scoped to the runtime's log-group name pattern instead and
    are intentionally excluded from this check."""
    template = _synth_template(fake_agent_zip)
    template_json = template.to_json()

    for statement in _all_policy_statements(template_json):
        if statement.get("Sid") not in _LEAST_PRIVILEGE_SIDS:
            continue
        resources = statement["Resource"]
        resources = resources if isinstance(resources, list) else [resources]
        for resource in resources:
            assert "*" not in _stringify(resource)


def test_no_policy_statement_grants_a_wildcard_action(fake_agent_zip: str) -> None:
    """CDK `grant_*()` helpers emit wildcard ACTIONS even when resources are scoped
    (Engram gotcha `gotchas/cdk-grant-wildcard-actions`) — assert no action in any
    statement in this stack ends with `*`."""
    template = _synth_template(fake_agent_zip)
    template_json = template.to_json()

    for statement in _all_policy_statements(template_json):
        actions = statement["Action"]
        actions = actions if isinstance(actions, list) else [actions]
        for action in actions:
            assert not action.endswith("*"), f"wildcard action found: {action}"


def test_trust_policy_scopes_to_this_account(fake_agent_zip: str) -> None:
    template = _synth_template(fake_agent_zip)

    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": {
                "Statement": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Effect": "Allow",
                                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                                "Condition": {
                                    "StringEquals": Match.object_like({"aws:SourceAccount": Match.any_value()}),
                                    "ArnLike": Match.object_like({"aws:SourceArn": Match.any_value()}),
                                },
                            }
                        )
                    ]
                )
            }
        },
    )


def test_environment_variables_carry_no_personal_values(fake_agent_zip: str) -> None:
    template = _synth_template(fake_agent_zip)
    (resource,) = template.find_resources("AWS::BedrockAgentCore::Runtime").values()
    env_vars = resource["Properties"]["EnvironmentVariables"]

    assert set(env_vars) == {"MODEL_ID", "KNOWLEDGE_BASE_ID", "AWS_REGION"}
    assert env_vars["MODEL_ID"] == NOVA_MICRO_INFERENCE_PROFILE_ID
    assert env_vars["AWS_REGION"] == "us-east-1"


def test_outputs_expose_runtime_arn_and_id(fake_agent_zip: str) -> None:
    template = _synth_template(fake_agent_zip)

    template.has_output("AgentRuntimeArn", {})
    template.has_output("AgentRuntimeId", {})


def test_missing_zip_raises_a_clear_error(tmp_path: Path) -> None:
    app = App()
    env = Environment(account="000000000000", region="us-east-1")
    data_stack = DataStack(app, "TestDataStack", env=env)

    with pytest.raises(FileNotFoundError, match="build_agent.sh"):
        AgentStack(app, "TestAgentStack", data=data_stack, agent_zip_path=str(tmp_path / "missing.zip"), env=env)


def _stringify(value: Any) -> str:
    """Flatten a resource value (literal or CFN intrinsic dict/list) to one string
    so a wildcard check does not need to know which shape it is."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "".join(_stringify(v) for v in value.values())
    if isinstance(value, list):
        return "".join(_stringify(v) for v in value)
    return str(value)
