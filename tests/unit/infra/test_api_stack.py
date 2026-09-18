"""RED tests for task 8.3: the CDK ApiStack (design.md SS3, SS4, SS9.2 — PR8b
boundary: Lambda function, its execution role, HTTP API with `POST /v1/chat`,
CORS, throttling, custom domain + ACM certificate, outputs).

Verified against AWS documentation before writing `infra/stacks/api_stack.py`
(see its module docstring for the cited URLs): `HttpLambdaIntegration`'s
default payload format version, the CORS-header precedence rule for HTTP
APIs, `AWS::ApiGatewayV2::Stage` throttle properties, and
`CertificateValidation.from_dns()` without a hosted zone.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest
from aws_cdk import App, Environment
from aws_cdk.assertions import Template

from infra.stacks.agent_stack import AgentStack
from infra.stacks.api_stack import (
    ALLOWED_METHODS,
    DOMAIN_NAME,
    FUNCTION_NAME,
    ApiStack,
)
from infra.stacks.data_stack import DataStack

_LEAST_PRIVILEGE_SIDS = {"InvokeAgentRuntime", "ReadWriteSessionsTable"}
_DEFAULT_ALLOWED_ORIGINS = ("https://sergiomondragon.com", "http://localhost:4321")


@pytest.fixture
def fake_agent_zip(tmp_path: Path) -> str:
    zip_path = tmp_path / "agent.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("main.py", "# fixture only\n")
    return str(zip_path)


@pytest.fixture
def fake_lambda_zip(tmp_path: Path) -> str:
    zip_path = tmp_path / "lambda.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("api/__init__.py", "")
        zf.writestr("api/handler.py", "def lambda_handler(event, context):\n    return {}\n")
    return str(zip_path)


def _build_stacks(agent_zip_path: str, lambda_zip_path: str) -> tuple[DataStack, AgentStack, ApiStack]:
    app = App()
    env = Environment(account="000000000000", region="us-east-1")
    data_stack = DataStack(app, "TestDataStack", env=env)
    agent_stack = AgentStack(app, "TestAgentStack", data=data_stack, agent_zip_path=agent_zip_path, env=env)
    api_stack = ApiStack(
        app,
        "TestApiStack",
        data=data_stack,
        agent=agent_stack,
        domain_name=DOMAIN_NAME,
        allowed_origins=_DEFAULT_ALLOWED_ORIGINS,
        lambda_zip_path=lambda_zip_path,
        env=env,
    )
    return data_stack, agent_stack, api_stack


def _synth_template(agent_zip_path: str, lambda_zip_path: str) -> Template:
    _, _, api_stack = _build_stacks(agent_zip_path, lambda_zip_path)
    return Template.from_stack(api_stack)


def _all_policy_statements(template_json: dict[str, Any]) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    for resource in template_json["Resources"].values():
        if resource["Type"] != "AWS::IAM::Policy":
            continue
        statements.extend(resource["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "".join(_stringify(v) for v in value.values())
    if isinstance(value, list):
        return "".join(_stringify(v) for v in value)
    return str(value)


def test_lambda_uses_lean_arm64_config(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    template = _synth_template(fake_agent_zip, fake_lambda_zip)

    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": FUNCTION_NAME,
            "Runtime": "python3.12",
            "Architectures": ["arm64"],
            "Handler": "api.handler.lambda_handler",
            "MemorySize": 512,
            "Timeout": 20,
            "ReservedConcurrentExecutions": 5,
        },
    )


def test_lambda_role_has_exactly_one_runtime_and_table_arn(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    template = _synth_template(fake_agent_zip, fake_lambda_zip)
    template_json = template.to_json()

    invoke_statement = next(s for s in _all_policy_statements(template_json) if s.get("Sid") == "InvokeAgentRuntime")
    assert invoke_statement["Effect"] == "Allow"
    assert invoke_statement["Action"] == "bedrock-agentcore:InvokeAgentRuntime"
    resources = invoke_statement["Resource"]
    assert isinstance(resources, list)
    assert len(resources) == 2  # the runtime ARN and its "/*" sub-resource wildcard

    table_statement = next(s for s in _all_policy_statements(template_json) if s.get("Sid") == "ReadWriteSessionsTable")
    assert table_statement["Effect"] == "Allow"
    # `Query` is deliberately absent: PR3b replaced the IP rate limiter's
    # sliding-window read with a point `GetItem` on the previous-minute
    # bucket (`src/api/adapters/dynamo_rate_limiter.py::_check_ip`), so
    # nothing in `src/api` ever issues a `Query` against this table.
    assert set(table_statement["Action"]) == {
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    }
    table_resource = table_statement["Resource"]
    assert not isinstance(table_resource, list)  # exactly one ARN, not a list of several


def test_lambda_role_has_no_wildcard_resource(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    """The `/*` suffix on the runtime ARN is a documented AWS requirement (PR5's
    apply-progress note: covers runtime-endpoint/qualifier sub-resources), not a
    least-privilege violation — a real ARN prefix followed by literal `/*`, never a
    bare `*` resource or wildcard action."""
    template = _synth_template(fake_agent_zip, fake_lambda_zip)
    template_json = template.to_json()

    for statement in _all_policy_statements(template_json):
        if statement.get("Sid") not in _LEAST_PRIVILEGE_SIDS:
            continue
        actions = statement["Action"]
        actions = actions if isinstance(actions, list) else [actions]
        for action in actions:
            assert not action.endswith("*"), f"wildcard action found: {action}"
        resources = statement["Resource"]
        resources = resources if isinstance(resources, list) else [resources]
        for resource in resources:
            stringified = _stringify(resource)
            assert stringified.count("*") <= 1, f"unexpected wildcard shape: {stringified}"


def test_lambda_role_writes_logs_only_to_its_own_log_group(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    """No `AWSLambdaBasicExecutionRole` managed policy (its `Resource: "*"` would
    violate least privilege) — an explicit `LogGroup` + a scoped policy instead."""
    template = _synth_template(fake_agent_zip, fake_lambda_zip)
    template_json = template.to_json()

    template.has_resource_properties(
        "AWS::Logs::LogGroup",
        {"LogGroupName": f"/aws/lambda/{FUNCTION_NAME}", "RetentionInDays": 30},
    )
    log_statement = next(s for s in _all_policy_statements(template_json) if s.get("Sid") == "WriteOwnLogGroup")
    assert set(log_statement["Action"]) == {"logs:CreateLogStream", "logs:PutLogEvents"}

    roles = template.find_resources("AWS::IAM::Role")
    assert len(roles) == 1, "expected exactly one IAM role (the Lambda execution role) in this stack"
    (role_resource,) = roles.values()
    managed_arns = role_resource["Properties"].get("ManagedPolicyArns", [])
    assert not any("AWSLambdaBasicExecutionRole" in _stringify(arn) for arn in managed_arns)


def test_environment_variables_carry_no_reserved_or_personal_keys(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    template = _synth_template(fake_agent_zip, fake_lambda_zip)
    (resource,) = template.find_resources("AWS::Lambda::Function").values()
    env_vars = resource["Properties"]["Environment"]["Variables"]

    assert set(env_vars) == {"TABLE_NAME", "AGENT_RUNTIME_ARN", "ALLOWED_ORIGINS"}
    assert "AWS_REGION" not in env_vars  # reserved by the Lambda runtime itself
    assert env_vars["ALLOWED_ORIGINS"] == ",".join(_DEFAULT_ALLOWED_ORIGINS)


def test_http_api_route_uses_payload_v2_lambda_integration(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    template = _synth_template(fake_agent_zip, fake_lambda_zip)

    template.has_resource_properties("AWS::ApiGatewayV2::Route", {"RouteKey": "POST /v1/chat"})
    template.has_resource_properties(
        "AWS::ApiGatewayV2::Integration",
        {
            "IntegrationType": "AWS_PROXY",
            "PayloadFormatVersion": "2.0",
        },
    )
    template.resource_count_is("AWS::ApiGatewayV2::Route", 1)


def test_cors_is_configured_on_the_http_api_with_credentials(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    """`chat-endpoint` spec, *CORS Restriction*: enabling CORS on the `HttpApi`
    itself (not the Lambda) is the deliberate PR8b decision — API Gateway
    ignores CORS headers returned from a backend integration once CORS is
    configured on the API, so ONE source of truth avoids the duplicate-header
    risk. See the module docstring for the cited AWS doc."""
    template = _synth_template(fake_agent_zip, fake_lambda_zip)

    template.has_resource_properties(
        "AWS::ApiGatewayV2::Api",
        {
            "CorsConfiguration": {
                "AllowCredentials": True,
                "AllowMethods": list(ALLOWED_METHODS),
                "AllowOrigins": list(_DEFAULT_ALLOWED_ORIGINS),
            }
        },
    )


def test_default_stage_has_throttle_settings(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    template = _synth_template(fake_agent_zip, fake_lambda_zip)

    template.has_resource_properties(
        "AWS::ApiGatewayV2::Stage",
        {
            "StageName": "$default",
            "AutoDeploy": True,
            "DefaultRouteSettings": {"ThrottlingBurstLimit": 20, "ThrottlingRateLimit": 10},
        },
    )


def test_custom_domain_configured_with_acm(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    template = _synth_template(fake_agent_zip, fake_lambda_zip)

    template.has_resource_properties("AWS::ApiGatewayV2::DomainName", {"DomainName": DOMAIN_NAME})
    template.has_resource_properties(
        "AWS::CertificateManager::Certificate",
        {"DomainName": DOMAIN_NAME, "ValidationMethod": "DNS"},
    )
    template.resource_count_is("AWS::ApiGatewayV2::ApiMapping", 1)


def test_outputs_expose_api_url_domain_and_certificate_hint(fake_agent_zip: str, fake_lambda_zip: str) -> None:
    template = _synth_template(fake_agent_zip, fake_lambda_zip)

    template.has_output("ApiUrl", {})
    template.has_output("ApiRegionalDomainName", {})
    template.has_output("CertificateValidationHint", {})
    template.has_output("FunctionName", {})


def test_missing_lambda_zip_raises_a_clear_error(fake_agent_zip: str, tmp_path: Path) -> None:
    app = App()
    env = Environment(account="000000000000", region="us-east-1")
    data_stack = DataStack(app, "TestDataStack", env=env)
    agent_stack = AgentStack(app, "TestAgentStack", data=data_stack, agent_zip_path=fake_agent_zip, env=env)

    with pytest.raises(FileNotFoundError, match="build_lambda.sh"):
        ApiStack(
            app,
            "TestApiStack",
            data=data_stack,
            agent=agent_stack,
            domain_name=DOMAIN_NAME,
            allowed_origins=_DEFAULT_ALLOWED_ORIGINS,
            lambda_zip_path=str(tmp_path / "missing.zip"),
            env=env,
        )
