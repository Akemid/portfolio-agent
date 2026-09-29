"""RED tests for task 7.2/7.3: the CDK DataStack (design.md SS3, SS8, SS9.2).

Verified against AWS documentation before writing this stack (see
`infra/stacks/data_stack.py` module docstring for the cited URLs): S3 Vectors
index dimension/distance-metric/data-type/non-filterable metadata keys, the
Bedrock KnowledgeBase S3_VECTORS storage shape, and the KB service role's
exact s3vectors action set.
"""

from __future__ import annotations

from aws_cdk import App, Environment
from aws_cdk.assertions import Match, Template

from api.adapters.dynamo_keys import PARTITION_KEY, SORT_KEY, TTL_ATTRIBUTE
from infra.stacks.data_stack import CONTENT_PREFIXES, EMBEDDING_DIMENSION, EMBEDDING_MODEL_ID, DataStack
from shared.names import CONTENT_DATA_SOURCE_NAME


def _synth_template() -> Template:
    app = App()
    stack = DataStack(
        app,
        "TestDataStack",
        env=Environment(account="000000000000", region="us-east-1"),
    )
    return Template.from_stack(stack)


def test_table_matches_the_shared_dynamo_keys_schema() -> None:
    """The table's key shape MUST mirror `dynamo_keys.py` / `dynamodb_schema.py`.

    This is the single-source-of-truth contract: a drift here would silently
    break every DynamoDB adapter written against those constants.
    """
    template = _synth_template()

    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "portfolio-agent-sessions",
            "BillingMode": "PAY_PER_REQUEST",
            "KeySchema": [
                {"AttributeName": PARTITION_KEY, "KeyType": "HASH"},
                {"AttributeName": SORT_KEY, "KeyType": "RANGE"},
            ],
            "AttributeDefinitions": Match.array_with(
                [
                    {"AttributeName": PARTITION_KEY, "AttributeType": "S"},
                    {"AttributeName": SORT_KEY, "AttributeType": "S"},
                ]
            ),
        },
    )


def test_table_has_ttl_enabled() -> None:
    template = _synth_template()

    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TimeToLiveSpecification": {
                "AttributeName": TTL_ATTRIBUTE,
                "Enabled": True,
            }
        },
    )


def test_bucket_and_table_removal_policy_is_retain() -> None:
    template = _synth_template()

    for resource_type in (
        "AWS::S3::Bucket",
        "AWS::DynamoDB::Table",
        "AWS::S3Vectors::VectorBucket",
        "AWS::S3Vectors::Index",
        "AWS::Bedrock::KnowledgeBase",
    ):
        resources = template.find_resources(resource_type)
        assert resources, f"expected at least one {resource_type} resource"
        for resource in resources.values():
            assert resource["DeletionPolicy"] == "Retain", resource_type
            assert resource["UpdateReplacePolicy"] == "Retain", resource_type


def test_content_bucket_is_private_encrypted_and_tls_only() -> None:
    template = _synth_template()

    template.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
            "BucketEncryption": {
                "ServerSideEncryptionConfiguration": Match.array_with(
                    [Match.object_like({"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}})]
                )
            },
            "VersioningConfiguration": {"Status": "Enabled"},
        },
    )
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Effect": "Deny",
                                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                                }
                            )
                        ]
                    )
                }
            )
        },
    )


def test_vector_index_dimension_and_metric_match_the_embedding_model() -> None:
    template = _synth_template()

    template.has_resource_properties(
        "AWS::S3Vectors::Index",
        {
            "DataType": "float32",
            "Dimension": EMBEDDING_DIMENSION,
            "DistanceMetric": "cosine",
            "MetadataConfiguration": {
                "NonFilterableMetadataKeys": Match.array_with(["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"])
            },
        },
    )
    template.has_resource_properties(
        "AWS::Bedrock::KnowledgeBase",
        {
            "KnowledgeBaseConfiguration": {
                "Type": "VECTOR",
                "VectorKnowledgeBaseConfiguration": Match.object_like(
                    {
                        "EmbeddingModelConfiguration": {
                            "BedrockEmbeddingModelConfiguration": {"Dimensions": EMBEDDING_DIMENSION}
                        }
                    }
                ),
            }
        },
    )


def test_knowledge_base_uses_s3_vectors_storage_and_titan_embeddings() -> None:
    assert EMBEDDING_MODEL_ID == "amazon.titan-embed-text-v2:0"
    template = _synth_template()

    # EmbeddingModelArn is built from the region token (an Fn::Join at synth time), so
    # it cannot be matched as a plain string here — the model id is asserted directly
    # above, and the ARN's construction is exercised by `test_app_synth_succeeds`
    # (a template with a malformed ARN token would fail synth/validation).
    template.has_resource_properties(
        "AWS::Bedrock::KnowledgeBase",
        {"StorageConfiguration": Match.object_like({"Type": "S3_VECTORS"})},
    )


def test_data_source_scopes_to_the_fixed_content_layout() -> None:
    template = _synth_template()

    template.has_resource_properties(
        "AWS::Bedrock::DataSource",
        {
            "DataSourceConfiguration": {
                "Type": "S3",
                "S3Configuration": Match.object_like({"InclusionPrefixes": CONTENT_PREFIXES}),
            }
        },
    )


def test_data_source_name_matches_the_shared_constant() -> None:
    """`scripts/sync_content.py` looks up this data source by name via
    `ListDataSources` — a drift here would silently break the manual sync
    procedure (`knowledge-base` spec, *Manual Sync Procedure*)."""
    template = _synth_template()

    template.has_resource_properties("AWS::Bedrock::DataSource", {"Name": CONTENT_DATA_SOURCE_NAME})


def test_kb_role_has_no_wildcard_resource() -> None:
    """No role in the stack (there is exactly one: the KB service role) grants `Resource: "*"`."""
    template = _synth_template()

    roles = template.find_resources("AWS::IAM::Role")
    assert roles, "expected the KB service role to exist"

    policies = template.find_resources("AWS::IAM::Policy")
    assert policies, "expected an inline policy attached to the KB service role"
    for policy in policies.values():
        for statement in policy["Properties"]["PolicyDocument"]["Statement"]:
            resource = statement["Resource"]
            resources = resource if isinstance(resource, list) else [resource]
            assert "*" not in resources, statement


def _all_policy_statements(template: Template) -> list[dict]:
    policies = template.find_resources("AWS::IAM::Policy")
    statements: list[dict] = []
    for policy in policies.values():
        statements.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return statements


def test_no_policy_statement_grants_a_wildcard_action() -> None:
    """`grant_read()`-style helpers emit wildcard ACTIONS (e.g. `s3:GetObject*`) even when

    the resource is scoped — a `Resource: "*"` check alone does not catch this. Every
    action string, in every statement, in every inline policy in the stack must be an
    exact IAM action with no trailing `*`.
    """
    template = _synth_template()

    for statement in _all_policy_statements(template):
        action = statement["Action"]
        actions = action if isinstance(action, list) else [action]
        for single_action in actions:
            assert not single_action.endswith("*"), statement


def test_kb_role_s3_permissions_are_scoped_to_content_prefix() -> None:
    """The KB role's S3 access is exactly: `GetObject` on `content/*` objects and

    `ListBucket` on the bucket scoped to the `content/*` prefix — per
    https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html. No
    `grant_read()`-style wildcard actions (`s3:GetObject*`, `s3:GetBucket*`,
    `s3:List*`) or unscoped `ListBucket`.
    """
    template = _synth_template()

    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 1, "expected exactly one S3 bucket (the content bucket)"
    bucket_logical_id = next(iter(buckets))
    bucket_arn = {"Fn::GetAtt": [bucket_logical_id, "Arn"]}
    content_objects_arn = {"Fn::Join": ["", [bucket_arn, "/content/*"]]}

    def _is_s3_statement(statement: dict) -> bool:
        action = statement["Action"]
        actions = action if isinstance(action, list) else [action]
        return any(single_action.startswith("s3:") for single_action in actions)

    s3_statements = [s for s in _all_policy_statements(template) if _is_s3_statement(s)]

    get_object_statements = [s for s in s3_statements if s["Action"] == "s3:GetObject"]
    assert len(get_object_statements) == 1, s3_statements
    assert get_object_statements[0]["Resource"] == content_objects_arn, get_object_statements[0]

    list_bucket_statements = [s for s in s3_statements if s["Action"] == "s3:ListBucket"]
    assert len(list_bucket_statements) == 1, s3_statements
    list_bucket_statement = list_bucket_statements[0]
    assert list_bucket_statement["Resource"] == bucket_arn, list_bucket_statement
    assert list_bucket_statement["Condition"] == {"StringLike": {"s3:prefix": ["content/*"]}}, list_bucket_statement

    assert len(s3_statements) == 2, s3_statements


def test_kb_role_trust_policy_scopes_to_bedrock_with_confused_deputy_conditions() -> None:
    template = _synth_template()

    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": Match.object_like(
                {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Principal": {"Service": "bedrock.amazonaws.com"},
                                    "Condition": Match.object_like(
                                        {
                                            "StringEquals": Match.object_like({"aws:SourceAccount": Match.any_value()}),
                                            "ArnLike": Match.object_like({"aws:SourceArn": Match.any_value()}),
                                        }
                                    ),
                                }
                            )
                        ]
                    )
                }
            )
        },
    )


def test_outputs_expose_ids_needed_by_later_stacks() -> None:
    template = _synth_template()

    outputs = template.find_outputs("*")
    assert set(outputs) == {"TableName", "ContentBucketName", "KnowledgeBaseId", "VectorBucketName"}
