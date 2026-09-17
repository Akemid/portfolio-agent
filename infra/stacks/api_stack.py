"""HTTP API + Lambda gate stack for the portfolio-agent migration (design.md
SS3, SS4, SS9.2 — PR8b: the ApiStack half of Phase 8, task 8.3/8.4b/8.5).

Creates:
- The Lambda function (`api.handler.lambda_handler`, lean package built by
  `scripts/build_lambda.sh`) with a least-privilege execution role and an
  explicitly provisioned, 30-day-retention CloudWatch Logs group.
- An `AWS::ApiGatewayV2::Api` (HTTP API) with exactly one route
  (`POST /v1/chat`, payload format version 2.0), CORS, and a throttled
  `$default` stage.
- A custom domain (`api.sergiomondragon.com`) backed by an ACM certificate
  requested with DNS validation and no Route 53 hosted zone (DNS lives at
  DigitalOcean — design.md SS DNS section).

Verified against AWS documentation before writing (URLs cited per resource):
- `HttpLambdaIntegration` defaults `payloadFormatVersion` to `2.0` — required
  so `request.requestContext.http.sourceIp`, `event.cookies`, and
  `event.rawPath` exist on the event `src/api/http/request_parser.py`
  already parses (format 1.0 lacks all three):
  https://docs.aws.amazon.com/cdk/api/v2/python/aws_cdk.aws_apigatewayv2_integrations/HttpLambdaIntegration.html
- **CORS decision (verified, not assumed)**: "If you configure CORS for an
  API, API Gateway ignores CORS headers returned from your backend
  integration" — this applies to every response, not only the `OPTIONS`
  preflight:
  https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html
  Decision: configure CORS on the `HttpApi` itself (`allow_origins` = the
  same `allowed_origins` list `Settings.from_env` reads), rather than relying
  on `src/api/http/responder.py`'s own `cors_headers()` (written in an
  earlier phase, before this interplay was verified against the HTTP API
  docs). This is ONE source of truth for the CORS headers a browser actually
  sees, so the two can never disagree or double up on
  `Access-Control-Allow-Origin` — the well-known failure mode when both a
  proxy integration and its Lambda try to own CORS. `responder.py`'s CORS
  headers become dead weight on the wire once this stack is deployed (API
  Gateway strips them before the client sees them) but are harmless and are
  NOT removed here — that is Lambda-code cleanup, out of this CDK-only PR's
  scope. `Set-Cookie` and every non-`Access-Control-*` header the Lambda
  returns (e.g. the session cookie) are untouched by this behavior; only the
  literal `Access-Control-*`/CORS-relevant response headers are ignored.
  Post-deploy smoke test (Phase 9) MUST confirm a real browser response
  carries exactly one `Access-Control-Allow-Origin` header, not two.
- `AWS::ApiGatewayV2::Stage` `DefaultRouteSettings` (`ThrottlingBurstLimit`,
  `ThrottlingRateLimit`) — a coarse, stage-wide cap under the design's USD 10
  cost ceiling, independent of the DynamoDB-backed per-session/per-IP limits:
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-apigatewayv2-stage.html
- `apigwv2.DomainName` + `acm.Certificate(validation=CertificateValidation.from_dns())`
  (no hosted zone — DNS validation CNAME must be added manually at
  DigitalOcean) + `default_domain_mapping` to the `$default` stage; the
  `DomainName.regional_domain_name` attribute is the CNAME target for the
  `api` record at DigitalOcean:
  https://docs.aws.amazon.com/cdk/api/v2/java/software/amazon/awscdk/services/apigatewayv2/package-summary.html
  https://docs.aws.amazon.com/cdk/api/v1/python/aws_cdk.aws_certificatemanager/CertificateValidation.html
- Lambda execution role: `bedrock-agentcore:InvokeAgentRuntime` on the
  runtime ARN AND `<runtime ARN>/*` (PR5's apply-progress note: the trailing
  wildcard covers runtime-endpoint/qualifier sub-resources a bare runtime ARN
  does not match — verified against
  https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html);
  `dynamodb:GetItem`/`PutItem`/`UpdateItem`/`Query` (PR3b's apply-progress
  note: `Query` is required for the IP rate limiter's sliding-window read) on
  exactly the one table ARN; explicit `logs:CreateLogStream`/`PutLogEvents`
  on this function's own log group, never the
  `AWSLambdaBasicExecutionRole` managed policy (its `Resource: "*"` would
  violate the `infrastructure` spec's *Least-Privilege Lambda Role*
  requirement) — same pattern as `agent_stack.py`'s `RuntimeLogGroup`/
  `RuntimeLogStream` statements.

**Route-405-vs-404 deviation from the `chat-endpoint` spec's literal scenario
text**: the spec's *Wrong method* scenario says `GET /v1/chat` -> `405
Method Not Allowed`. API Gateway HTTP API v2 has no native "method exists on
this path but not this verb" distinction — an unmatched route (any method,
any path) always returns a generic `404 Not Found`
(https://docs.aws.amazon.com/powertools/python/1.24.0/core/event_handler/api_gateway/index.html,
"By default, we return 404 for any unmatched route", consistent with HTTP
API's documented routing model). Producing a literal `405` would require an
`ANY /v1/chat` catch-all route forwarding every method to the Lambda plus
new method-dispatch logic inside `lambda_handler` — outside this PR's
Lambda/HTTP-API-wiring boundary (task 8.3's own RED test list does not
include one, and the top-level apply prompt describes the acceptance as
"other methods 405/404 by routing", accepting either). This stack therefore
defines exactly the one `POST /v1/chat` route and relies on the native `404`
for every other method/path combination — a genuine spec/implementation gap,
recorded here and in apply-progress rather than silently "fixed" with
un-budgeted Lambda code changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_apigatewayv2_integrations as apigwv2_integrations
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

from infra.stacks.agent_stack import AgentStack
from infra.stacks.data_stack import DataStack

FUNCTION_NAME = "portfolio-agent-chat"
HANDLER = "api.handler.lambda_handler"
LAMBDA_RUNTIME = lambda_.Runtime.PYTHON_3_12
LAMBDA_MEMORY_MB = 512
# Above the AgentCoreClient's 12s read_timeout (PR5) with headroom for cold
# start and DynamoDB round trips, comfortably under API Gateway HTTP API's
# 30s integration cap (PR5's apply-progress `lambda_timeout_recommendation`).
LAMBDA_TIMEOUT = Duration.seconds(20)
# Hard cost cap (design.md's USD 10 ceiling): bounds the worst-case concurrent
# AgentCore/Bedrock spend regardless of traffic spikes or a rate-limit bypass bug.
LAMBDA_RESERVED_CONCURRENCY = 5
LOG_RETENTION = logs.RetentionDays.ONE_MONTH

ROUTE_PATH = "/v1/chat"
DOMAIN_NAME = "api.sergiomondragon.com"
ALLOWED_METHODS = (apigwv2.CorsHttpMethod.POST,)
ALLOWED_HEADERS = ["content-type"]
CORS_MAX_AGE = Duration.seconds(300)
# Coarse, stage-wide global cap under the cost ceiling — independent of the
# DynamoDB-backed per-session (10/day) and per-IP (5/min) limits, which are
# the primary defense; this is a backstop against both exceeding traffic and
# a bypass of those checks.
THROTTLE_BURST_LIMIT = 20
THROTTLE_RATE_LIMIT = 10


class ApiStack(Stack):
    """Lambda gate, HTTP API, CORS, throttling, and the custom domain."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data: DataStack,
        agent: AgentStack,
        domain_name: str,
        allowed_origins: Sequence[str],
        lambda_zip_path: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.data = data
        self.agent = agent
        self._allowed_origins = list(allowed_origins)

        self.log_group = self._build_log_group()
        self.lambda_role = self._build_lambda_role()
        self.function = self._build_function(lambda_zip_path)
        self.certificate = self._build_certificate(domain_name)
        self.domain_name_resource = self._build_domain_name(domain_name, self.certificate)
        self.http_api = self._build_http_api()
        self._build_route()
        self._build_default_stage()

        self._build_outputs()

    def _build_log_group(self) -> logs.LogGroup:
        # Logs are ephemeral operational data, unlike the RETAIN-policy content
        # bucket/session table (`infrastructure` spec, *Data Retention on
        # Destroy* only names those two) — DESTROY is appropriate so `cdk
        # destroy` leaves no orphaned log group behind.
        return logs.LogGroup(
            self,
            "ChatFunctionLogGroup",
            log_group_name=f"/aws/lambda/{FUNCTION_NAME}",
            retention=LOG_RETENTION,
            removal_policy=RemovalPolicy.DESTROY,
        )

    def _build_lambda_role(self) -> iam.Role:
        role = iam.Role(self, "ChatFunctionRole", assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"))
        role.add_to_policy(
            iam.PolicyStatement(
                sid="InvokeAgentRuntime",
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=[self.agent.runtime_arn, f"{self.agent.runtime_arn}/*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadWriteSessionsTable",
                actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"],
                resources=[self.data.table.table_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteOwnLogGroup",
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"{self.log_group.log_group_arn}:*"],
            )
        )
        return role

    def _build_function(self, lambda_zip_path: str) -> lambda_.Function:
        if not Path(lambda_zip_path).is_file():
            raise FileNotFoundError(
                f"Lambda deployment package not found at {lambda_zip_path!r}. "
                "Run `scripts/build_lambda.sh` first to build build/lambda.zip "
                "(or point LAMBDA_ZIP_PATH at an existing zip)."
            )
        return lambda_.Function(
            self,
            "ChatFunction",
            function_name=FUNCTION_NAME,
            runtime=LAMBDA_RUNTIME,
            architecture=lambda_.Architecture.ARM_64,
            handler=HANDLER,
            code=lambda_.Code.from_asset(lambda_zip_path),
            memory_size=LAMBDA_MEMORY_MB,
            timeout=LAMBDA_TIMEOUT,
            reserved_concurrent_executions=LAMBDA_RESERVED_CONCURRENCY,
            role=self.lambda_role,
            log_group=self.log_group,
            environment=self._environment_variables(),
        )

    def _environment_variables(self) -> dict[str, str]:
        # `Settings.from_env` (src/api/config.py) already defaults
        # SESSION_DAILY_LIMIT/IP_MINUTE_LIMIT/COOKIE_SECURE/LOG_LEVEL/
        # AGENT_QUALIFIER to spec-correct values, so setting them here would
        # just duplicate a value that can drift from the code (same
        # minimization convention as `agent_stack.py`'s `EnvironmentVariables`).
        # `AWS_REGION` is a reserved Lambda runtime env var and MUST NOT be
        # set explicitly — `Settings.from_env` reads it from the runtime.
        return {
            "TABLE_NAME": self.data.table.table_name,
            "AGENT_RUNTIME_ARN": self.agent.runtime_arn,
            "ALLOWED_ORIGINS": ",".join(self._allowed_origins),
        }

    def _build_certificate(self, domain_name: str) -> acm.Certificate:
        # DNS validation with NO hosted zone: `sergiomondragon.com` is at
        # DigitalOcean, not Route 53 (design.md DNS section). This means the
        # stack cannot auto-create the validation CNAME — deploying it will
        # not finish creating the certificate until that record is added
        # manually. **Manual step (one-time, before first deploy)**:
        #   1. `aws acm describe-certificate --certificate-arn <this
        #      certificate's ARN, see the CertificateValidationHint output>
        #      --region us-east-1 --query DomainValidationOptions`
        #   2. Create the returned CNAME (name -> value) at DigitalOcean's DNS
        #      panel for `sergiomondragon.com`.
        #   3. Wait for the certificate status to become ISSUED (ACM polls
        #      DNS automatically once the record exists).
        # See the Phase 9 runbook for the full one-time DNS procedure,
        # including the separate `api` CNAME (ApiRegionalDomainName output).
        return acm.Certificate(
            self,
            "ApiCertificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(),
        )

    def _build_domain_name(self, domain_name: str, certificate: acm.Certificate) -> apigwv2.DomainName:
        return apigwv2.DomainName(self, "ApiDomainName", domain_name=domain_name, certificate=certificate)

    def _build_http_api(self) -> apigwv2.HttpApi:
        return apigwv2.HttpApi(
            self,
            "ChatHttpApi",
            create_default_stage=False,
            cors_preflight=apigwv2.CorsPreflightOptions(
                allow_origins=self._allowed_origins,
                allow_methods=list(ALLOWED_METHODS),
                allow_headers=ALLOWED_HEADERS,
                allow_credentials=True,
                max_age=CORS_MAX_AGE,
            ),
        )

    def _build_route(self) -> None:
        integration = apigwv2_integrations.HttpLambdaIntegration("ChatIntegration", self.function)
        self.http_api.add_routes(path=ROUTE_PATH, methods=[apigwv2.HttpMethod.POST], integration=integration)

    def _build_default_stage(self) -> apigwv2.HttpStage:
        return apigwv2.HttpStage(
            self,
            "DefaultStage",
            http_api=self.http_api,
            auto_deploy=True,
            throttle=apigwv2.ThrottleSettings(burst_limit=THROTTLE_BURST_LIMIT, rate_limit=THROTTLE_RATE_LIMIT),
            domain_mapping=apigwv2.DomainMappingOptions(domain_name=self.domain_name_resource),
        )

    def _build_outputs(self) -> None:
        CfnOutput(self, "ApiUrl", value=self.http_api.api_endpoint, export_name=f"{self.stack_name}-ApiUrl")
        CfnOutput(
            self,
            "ApiRegionalDomainName",
            value=self.domain_name_resource.regional_domain_name,
            export_name=f"{self.stack_name}-ApiRegionalDomainName",
        )
        CfnOutput(
            self,
            "CertificateValidationHint",
            value=self.certificate.certificate_arn,
            description=(
                "Run `aws acm describe-certificate --certificate-arn <this value> "
                "--region us-east-1 --query DomainValidationOptions` to get the DNS "
                "validation CNAME record, then create it at DigitalOcean."
            ),
            export_name=f"{self.stack_name}-CertificateValidationHint",
        )
        CfnOutput(
            self,
            "FunctionName",
            value=self.function.function_name,
            export_name=f"{self.stack_name}-FunctionName",
        )
