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
