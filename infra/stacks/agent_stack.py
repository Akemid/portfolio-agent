"""AgentCore Runtime stack for the portfolio-agent migration (design.md SS3, SS5,
SS8 RQ-1/RQ-2, SS9.2).

PR8a scope only: the agent code asset, the `AWS::BedrockAgentCore::Runtime` (direct
code deployment — no container, no ECR), and its execution role. PR8b adds
`ApiStack` (Lambda, HTTP API) separately.

Verified against AWS documentation before writing (URLs cited per resource):
- `AWS::BedrockAgentCore::Runtime` properties, `Ref`/`Fn::GetAtt` return values
  (`Ref` returns the runtime ARN; `Fn::GetAtt` exposes `AgentRuntimeArn`,
  `AgentRuntimeId`, `AgentRuntimeVersion`), and `AgentRuntimeName`'s allowed pattern
  (letters/digits/underscore only, starting with a letter, max 48 chars — no
  hyphens, unlike the rest of this repo's kebab-case resource names):
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html
- `CodeConfiguration` (`Code.S3{Bucket,Prefix}`, `EntryPoint`, `Runtime`) and
  `NetworkConfiguration.NetworkMode` (`PUBLIC | VPC`):
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrockagentcore-runtime-codeconfiguration.html
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrockagentcore-runtime-networkconfiguration.html
- Direct code deployment for Python: `EntryPoint: ["main.py"]` at the zip root,
  `networkConfiguration: {"networkMode": "PUBLIC"}`, AgentCore Runtime supports only
  **arm64** deployment packages (enforced by `scripts/build_agent.sh`), and the
  console/CLI both state the execution role needs "CloudWatch Logs permissions for
  observability":
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-code-deploy-python.html
- Execution role + trust policy for the **direct-deploy** execution role (distinct
  from the container-based "AgentCore Runtime execution role" example on the same
  page, which additionally lists ECR and workload-identity-token actions that a
  direct-code, no-OAuth-tool agent like this one does not need):
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html

Deviation from design.md SS9.2's illustrative 3-statement IAM sketch: this role also
grants the four CloudWatch Logs actions the docs above list as required for the
runtime to create its own log group/stream and write log events, scoped to the
`/aws/bedrock-agentcore/runtimes/*` ARN pattern (never a bare `"*"` resource). The
docs' broader `logs:DescribeLogGroups` (needs an account-wide `log-group:*`
resource) and `logs:PutResourcePolicy` (a log-group *admin* action, not something a
running agent needs to emit its own logs), plus X-Ray and `cloudwatch:PutMetricData`
(unused observability features in v1), are intentionally NOT granted — trimmed
further than AWS's generic template, consistent with the `infrastructure` spec's
*Least-Privilege Agent Role* requirement. This is a documented "to verify at
deploy" risk: if the managed runtime's internal logging path calls
`logs:DescribeLogGroups` before falling back to `CreateLogGroup`, a denied call
could surface as a warning or (worst case) a missing log group — it must not affect
`agent_invocation`'s response, since `src/agent/main.py` never raises on a caught
exception, but this has not been observed against a real deployment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import Aws, CfnOutput, Stack
from aws_cdk import aws_bedrockagentcore as bedrockagentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

from infra.stacks.data_stack import DataStack

# `^[a-zA-Z][a-zA-Z0-9_]{0,47}$` per the CFN reference above — no hyphens.
AGENT_RUNTIME_NAME = "portfolio_agent_runtime"
AGENT_RUNTIME_LANGUAGE = "PYTHON_3_12"
# `main.py` at the zip root (design.md RQ-2 packaging flow / `scripts/build_agent.sh`),
# never `agent.main` — the direct-deploy entrypoint examples reference the file name.
ENTRY_POINT = ["main.py"]

# design.md SS8 RQ-1: base on-demand id and the US cross-region inference profile.
# The agent role grants InvokeModel on BOTH ARNs regardless of which one `MODEL_ID`
# is actually set to, so switching between them at deploy time needs no role change.
NOVA_MICRO_MODEL_ID = "amazon.nova-micro-v1:0"
NOVA_MICRO_INFERENCE_PROFILE_ID = "us.amazon.nova-micro-v1:0"

_RUNTIME_LOG_GROUP_ARN_PATTERN = (
    f"arn:aws:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"
)


class AgentStack(Stack):
    """Agent code asset, `CfnRuntime`, and the agent execution role."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        data: DataStack,
        agent_zip_path: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.data = data

        self.agent_asset = self._build_agent_asset(agent_zip_path)
        self.agent_role = self._build_agent_role()
        self.runtime = self._build_runtime()
        self.runtime_arn = self.runtime.attr_agent_runtime_arn

        self._build_outputs()

    def _build_agent_asset(self, agent_zip_path: str) -> s3_assets.Asset:
        if not Path(agent_zip_path).is_file():
            raise FileNotFoundError(
                f"Agent deployment package not found at {agent_zip_path!r}. "
                "Run `scripts/build_agent.sh` first to build build/agent.zip "
                "(or point AGENT_ZIP_PATH at an existing zip)."
            )
        return s3_assets.Asset(self, "AgentCodeAsset", path=agent_zip_path)

    def _build_agent_role(self) -> iam.Role:
        role = iam.Role(
            self,
            "AgentExecutionRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{Aws.REGION}:{Aws.ACCOUNT_ID}:*"},
                },
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="AnswerModelOnly",
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{Aws.REGION}::foundation-model/{NOVA_MICRO_MODEL_ID}",
                    f"arn:aws:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:inference-profile/{NOVA_MICRO_INFERENCE_PROFILE_ID}",
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="RetrieveOneKb",
                actions=["bedrock:Retrieve"],
                resources=[self.data.knowledge_base.attr_knowledge_base_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadOwnCode",
                actions=["s3:GetObject"],
                resources=[self.agent_asset.bucket.arn_for_objects(self.agent_asset.s3_object_key)],
            )
        )
        # See the module docstring's "Deviation from design.md SS9.2" note.
        role.add_to_policy(
            iam.PolicyStatement(
                sid="RuntimeLogGroup",
                actions=["logs:CreateLogGroup", "logs:DescribeLogStreams"],
                resources=[_RUNTIME_LOG_GROUP_ARN_PATTERN],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="RuntimeLogStream",
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[f"{_RUNTIME_LOG_GROUP_ARN_PATTERN}:log-stream:*"],
            )
        )
        return role

    def _build_runtime(self) -> bedrockagentcore.CfnRuntime:
        return bedrockagentcore.CfnRuntime(
            self,
            "AgentRuntime",
            agent_runtime_name=AGENT_RUNTIME_NAME,
            agent_runtime_artifact=bedrockagentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                code_configuration=bedrockagentcore.CfnRuntime.CodeConfigurationProperty(
                    code=bedrockagentcore.CfnRuntime.CodeProperty(
                        s3=bedrockagentcore.CfnRuntime.S3LocationProperty(
                            bucket=self.agent_asset.s3_bucket_name,
                            prefix=self.agent_asset.s3_object_key,
                        )
                    ),
                    entry_point=ENTRY_POINT,
                    runtime=AGENT_RUNTIME_LANGUAGE,
                )
            ),
            role_arn=self.agent_role.role_arn,
            network_configuration=bedrockagentcore.CfnRuntime.NetworkConfigurationProperty(network_mode="PUBLIC"),
            # Non-streaming v1 (`str(agent(prompt))`, design.md SS9.2); HTTP protocol,
            # IAM/SigV4 authorization left at the default (no AuthorizerConfiguration).
            protocol_configuration="HTTP",
            environment_variables={
                "MODEL_ID": NOVA_MICRO_INFERENCE_PROFILE_ID,
                "KNOWLEDGE_BASE_ID": self.data.knowledge_base.attr_knowledge_base_id,
                "AWS_REGION": self.region,
                # RETRIEVAL_TOP_K / MAX_TOKENS / TEMPERATURE / MAX_ANSWER_CHARS are
                # deliberately left unset — `AgentSettings.from_env` (src/agent/settings.py)
                # already defines and clamps sensible defaults for all four, so setting
                # them here would just duplicate a value that can drift from the code.
            },
        )

    def _build_outputs(self) -> None:
        CfnOutput(
            self,
            "AgentRuntimeArn",
            value=self.runtime.attr_agent_runtime_arn,
            export_name=f"{self.stack_name}-AgentRuntimeArn",
        )
        CfnOutput(
            self,
            "AgentRuntimeId",
            value=self.runtime.attr_agent_runtime_id,
            export_name=f"{self.stack_name}-AgentRuntimeId",
        )
