"""Data layer for the portfolio-agent AgentCore migration (design.md SS3, SS8, SS9.2).

Creates:
- S3 content bucket: private, versioned, SSE-S3, TLS-enforced. Layout is fixed by the
  `knowledge-base` spec (*S3 Data Source Layout*): `content/cv/*.pdf` and
  `content/portfolio/{en,es}/*.md`.
- S3 Vectors bucket + index (1024 dims, cosine) backing the Knowledge Base
  (design.md SS8 RQ-1: 1024 dims chosen over 256/512 — recall loss is not worth the
  ~1 MB storage saving at this corpus size).
- DynamoDB single table (`portfolio-agent-sessions`), mirroring
  `tests/helpers/dynamodb_schema.py` / `src/api/adapters/dynamo_keys.py` exactly — the
  single-source-of-truth contract enforced by `test_table_matches_the_shared_dynamo_keys_schema`.
- Bedrock Knowledge Base (S3_VECTORS storage, Titan Text Embeddings V2), its S3 data
  source, and a least-privilege service role (design.md SS9.2).

All data resources use `RemovalPolicy.RETAIN` (`infrastructure` spec, *Data Retention
on Destroy*) so `cdk destroy` never loses knowledge base content or session/rate-limit
counters.

Verified against AWS documentation before writing (URLs cited per resource):
- `AWS::S3Vectors::VectorBucket` / `AWS::S3Vectors::Index` properties (DataType is
  `float32`-only, DistanceMetric `cosine|euclidean`, Dimension 1-4096):
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-s3vectors-index.html
- A vector index pre-created for a Bedrock Knowledge Base MUST declare
  `AMAZON_BEDROCK_TEXT` and `AMAZON_BEDROCK_METADATA` as non-filterable metadata keys:
  https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html
- `AWS::Bedrock::KnowledgeBase` `StorageConfiguration.Type = S3_VECTORS` +
  `S3VectorsConfiguration` (vectorBucketArn + indexArn), and
  `VectorKnowledgeBaseConfiguration.EmbeddingModelConfiguration.BedrockEmbeddingModelConfiguration.Dimensions`
  (must match the index's `Dimension`):
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-bedrock-knowledgebase-s3vectorsconfiguration.html
- `AWS::Bedrock::DataSource` `S3Configuration` (`BucketArn`, `InclusionPrefixes`):
  https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrock-knowledgebase.html
- KB service role: exactly 5 `s3vectors:*` actions on the one index ARN (no
  `ListVectors` — that permission is for listing vector *keys*, not KB retrieval), KB
  trust policy with `aws:SourceAccount`/`aws:SourceArn` confused-deputy conditions:
  https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html
"""

from __future__ import annotations

from typing import Any

from aws_cdk import Aws, CfnOutput, RemovalPolicy, Stack
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3vectors as s3vectors
from constructs import Construct

from api.adapters.dynamo_keys import PARTITION_KEY, SORT_KEY, TTL_ATTRIBUTE

TABLE_NAME = "portfolio-agent-sessions"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024  # design.md SS8 RQ-1
VECTOR_INDEX_NAME = "portfolio-agent-index"
# `S3DataSourceConfiguration.InclusionPrefixes` allows at most one entry (verified via
# AWS docs: https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_bedrock.CfnDataSource.S3DataSourceConfigurationProperty.html),
# so a single top-level prefix scopes ingestion to `content/cv/` and
# `content/portfolio/{en,es}/` together (`knowledge-base` spec, *S3 Data Source Layout*).
CONTENT_PREFIXES = ["content/"]
NON_FILTERABLE_METADATA_KEYS = ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"]
KB_VECTOR_ACTIONS = [
    "s3vectors:PutVectors",
    "s3vectors:GetVectors",
    "s3vectors:DeleteVectors",
    "s3vectors:QueryVectors",
    "s3vectors:GetIndex",
]


class DataStack(Stack):
    """S3 content + vectors, DynamoDB table, and the Bedrock Knowledge Base."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Derived from the CloudFormation account pseudo-parameter (never a literal in
        # this public repo): unique without revealing the account id in source, since
        # it only resolves to a concrete value at deploy time.
        self.vector_bucket_name: str = f"portfolio-agent-vectors-{Aws.ACCOUNT_ID}"

        self.content_bucket = self._build_content_bucket()
        self.table = self._build_table()
        self.vector_bucket = self._build_vector_bucket()
        self.vector_index = self._build_vector_index()

        kb_role = self._build_kb_role()
        self.knowledge_base = self._build_knowledge_base(kb_role)
        self._build_data_source()

        self._build_outputs()

    def _build_content_bucket(self) -> s3.Bucket:
        return s3.Bucket(
            self,
            "ContentBucket",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

    def _build_table(self) -> dynamodb.Table:
        return dynamodb.Table(
            self,
            "SessionsTable",
            table_name=TABLE_NAME,
            partition_key=dynamodb.Attribute(name=PARTITION_KEY, type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name=SORT_KEY, type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute=TTL_ATTRIBUTE,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True,
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

    def _build_vector_bucket(self) -> s3vectors.CfnVectorBucket:
        bucket = s3vectors.CfnVectorBucket(self, "VectorBucket", vector_bucket_name=self.vector_bucket_name)
        bucket.apply_removal_policy(RemovalPolicy.RETAIN)
        return bucket

    def _build_vector_index(self) -> s3vectors.CfnIndex:
        index = s3vectors.CfnIndex(
            self,
            "VectorIndex",
            vector_bucket_name=self.vector_bucket_name,
            index_name=VECTOR_INDEX_NAME,
            data_type="float32",
            dimension=EMBEDDING_DIMENSION,
            distance_metric="cosine",
            metadata_configuration=s3vectors.CfnIndex.MetadataConfigurationProperty(
                non_filterable_metadata_keys=NON_FILTERABLE_METADATA_KEYS,
            ),
        )
        index.add_resource_dependency(self.vector_bucket)
        index.apply_removal_policy(RemovalPolicy.RETAIN)
        return index

    def _build_kb_role(self) -> iam.Role:
        role = iam.Role(
            self,
            "KnowledgeBaseRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:knowledge-base/*"},
                },
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EmbedTitanOnly",
                actions=["bedrock:InvokeModel"],
                resources=[f"arn:aws:bedrock:{Aws.REGION}::foundation-model/{EMBEDDING_MODEL_ID}"],
            )
        )
        self.content_bucket.grant_read(role)
        role.add_to_policy(
            iam.PolicyStatement(
                sid="OneVectorIndex",
                actions=KB_VECTOR_ACTIONS,
                resources=[self.vector_index.attr_index_arn],
            )
        )
        return role

    def _build_knowledge_base(self, role: iam.Role) -> bedrock.CfnKnowledgeBase:
        knowledge_base = bedrock.CfnKnowledgeBase(
            self,
            "KnowledgeBase",
            name="portfolio-agent-kb",
            role_arn=role.role_arn,
            knowledge_base_configuration=bedrock.CfnKnowledgeBase.KnowledgeBaseConfigurationProperty(
                type="VECTOR",
                vector_knowledge_base_configuration=bedrock.CfnKnowledgeBase.VectorKnowledgeBaseConfigurationProperty(
                    embedding_model_arn=f"arn:aws:bedrock:{Aws.REGION}::foundation-model/{EMBEDDING_MODEL_ID}",
                    embedding_model_configuration=bedrock.CfnKnowledgeBase.EmbeddingModelConfigurationProperty(
                        bedrock_embedding_model_configuration=(
                            bedrock.CfnKnowledgeBase.BedrockEmbeddingModelConfigurationProperty(
                                dimensions=EMBEDDING_DIMENSION,
                            )
                        )
                    ),
                ),
            ),
            storage_configuration=bedrock.CfnKnowledgeBase.StorageConfigurationProperty(
                type="S3_VECTORS",
                s3_vectors_configuration=bedrock.CfnKnowledgeBase.S3VectorsConfigurationProperty(
                    vector_bucket_arn=self.vector_bucket.attr_vector_bucket_arn,
                    index_arn=self.vector_index.attr_index_arn,
                ),
            ),
        )
        # No explicit add_resource_dependency: the Fn::GetAtt on vector_index.attr_index_arn
        # above already creates an implicit CloudFormation dependency (cfn-lint W3005).
        knowledge_base.apply_removal_policy(RemovalPolicy.RETAIN)
        return knowledge_base

    def _build_data_source(self) -> bedrock.CfnDataSource:
        data_source = bedrock.CfnDataSource(
            self,
            "ContentDataSource",
            knowledge_base_id=self.knowledge_base.attr_knowledge_base_id,
            name="portfolio-agent-content",
            data_source_configuration=bedrock.CfnDataSource.DataSourceConfigurationProperty(
                type="S3",
                s3_configuration=bedrock.CfnDataSource.S3DataSourceConfigurationProperty(
                    bucket_arn=self.content_bucket.bucket_arn,
                    inclusion_prefixes=CONTENT_PREFIXES,
                ),
            ),
        )
        data_source.apply_removal_policy(RemovalPolicy.RETAIN)
        return data_source

    def _build_outputs(self) -> None:
        CfnOutput(self, "TableName", value=self.table.table_name, export_name=f"{self.stack_name}-TableName")
        CfnOutput(
            self,
            "ContentBucketName",
            value=self.content_bucket.bucket_name,
            export_name=f"{self.stack_name}-ContentBucketName",
        )
        CfnOutput(
            self,
            "KnowledgeBaseId",
            value=self.knowledge_base.attr_knowledge_base_id,
            export_name=f"{self.stack_name}-KnowledgeBaseId",
        )
        CfnOutput(
            self,
            "VectorBucketName",
            value=self.vector_bucket_name,
            export_name=f"{self.stack_name}-VectorBucketName",
        )
