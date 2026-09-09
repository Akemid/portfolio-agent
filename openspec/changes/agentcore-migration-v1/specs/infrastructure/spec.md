# Infrastructure Specification

## Purpose

Defines how the stack is defined as code, its IAM boundaries, CI quality gates, data retention, and domain setup, so the whole system can be recreated from scratch and audited for least privilege.

## Requirements

### Requirement: Infrastructure as Code

All AWS resources (API Gateway, Lambda, DynamoDB, S3, Knowledge Base, AgentCore Runtime, IAM) MUST be defined in AWS CDK (Python) unless design records an explicit exception for a resource CDK cannot express. No resource MUST be created or modified manually in the AWS Console for a stack that is meant to be reproducible.

#### Scenario: Full stack recreation

- GIVEN a clean AWS account
- WHEN the CDK app is deployed
- THEN every resource in scope exists, with only the content upload performed manually

### Requirement: Least-Privilege Lambda Role

The Lambda's execution role MUST grant `InvokeAgentRuntime` on exactly one named AgentCore Runtime ARN and read/write access to exactly one named DynamoDB table. It MUST NOT grant broader Bedrock, DynamoDB, or S3 permissions.

#### Scenario: Lambda role scope

- GIVEN the Lambda execution role's policy
- WHEN it is inspected
- THEN it references exactly one Runtime ARN and exactly one table ARN, with no wildcard resource

### Requirement: Least-Privilege Agent Role

The AgentCore Runtime's execution role MUST grant `Retrieve` on exactly one named Knowledge Base and `InvokeModel` on exactly the chosen model IDs (Nova Micro, Titan Text Embeddings V2). It MUST NOT grant broader Bedrock or S3 permissions.

#### Scenario: Agent role scope

- GIVEN the agent runtime's execution role policy
- WHEN it is inspected
- THEN it references exactly one Knowledge Base ARN and the specific model IDs in use, with no wildcard resource

### Requirement: No Secrets in Repository

The repository MUST NOT contain AWS credentials, API keys, or other secrets. Configuration that varies by environment MUST be supplied through CDK context, environment variables, or a secrets manager, never hardcoded.

#### Scenario: Secret scan passes

- GIVEN a pull request
- WHEN CI runs the secret scanner
- THEN it reports no findings

### Requirement: CI Quality Gates

CI MUST run and block merge on: `ruff check` and `ruff format --check`, `pytest` with `--cov-fail-under=85`, a secret scan, and a dependency scan. A pull request MUST NOT be merged if any of these fail.

#### Scenario: Coverage gate fails the build

- GIVEN a pull request drops line coverage on `src/` below 85 %
- WHEN CI runs the coverage check
- THEN the build fails and the PR cannot merge

#### Scenario: All gates pass

- GIVEN a pull request meets lint, format, coverage, secret-scan, and dependency-scan requirements
- WHEN CI runs
- THEN the build succeeds and the PR is mergeable

### Requirement: Data Retention on Destroy

The S3 content bucket and the DynamoDB rate-limit/session table MUST use a `RETAIN` removal policy, so that destroying the application stack does not delete knowledge content or in-flight counters.

#### Scenario: Stack destroy preserves data

- GIVEN the application stack is destroyed via `cdk destroy`
- WHEN the S3 bucket and DynamoDB table are checked afterward
- THEN both still exist with their data intact

### Requirement: Custom Domain

The API MUST be served at `api.sergiomondragon.com` via a custom domain mapping on API Gateway, backed by an ACM certificate.

#### Scenario: Custom domain resolves

- GIVEN the stack is deployed and DNS is configured
- WHEN a client resolves `api.sergiomondragon.com`
- THEN it reaches the API Gateway HTTP API over a valid TLS certificate

### Requirement: Monthly Cost Ceiling

The infrastructure design SHOULD keep estimated monthly AWS cost under USD 10 at expected portfolio traffic, using pay-per-use services (DynamoDB on-demand, S3 Vectors, Lambda) and the cheapest suitable model tier (Nova Micro).

#### Scenario: Cost estimate documented

- GIVEN the design phase
- WHEN the cost estimate is produced
- THEN it is documented and stays under USD 10/month at expected traffic
