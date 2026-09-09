# Knowledge Base Specification

## Purpose

Defines the S3-backed content layout, ingestion procedure, embeddings, and retrieval expectations for the Bedrock Knowledge Base that grounds chat answers, without ever committing personal content to the repository.

## Requirements

### Requirement: S3 Data Source Layout

The knowledge base's S3 data source MUST follow a fixed layout: `content/cv/*.pdf` for the CV, and `content/portfolio/{en,es}/*.md` for portfolio content, separated by language.

#### Scenario: Layout used for ingestion

- GIVEN content has been uploaded to S3
- WHEN the Knowledge Base ingests the data source
- THEN it reads CV PDFs from `content/cv/` and portfolio Markdown from `content/portfolio/en/` and `content/portfolio/es/`

### Requirement: Content Never Committed

Personal content (CV PDF, portfolio Markdown under `content/`) MUST NOT exist in the `portfolio-agent` repository or its git history. The repository MUST `.gitignore` these paths, and CI MUST fail a pull request that introduces a file under a content path.

#### Scenario: Gitignored content path

- GIVEN `.gitignore` lists the content paths
- WHEN a developer runs `git status` after placing a CV PDF locally
- THEN the file does not appear as trackable

#### Scenario: CI content check

- GIVEN a pull request adds a file under `content/cv/` or `content/portfolio/`
- WHEN CI runs the content check
- THEN CI fails the build

### Requirement: Manual Sync Procedure

Content updates MUST follow a documented, out-of-band procedure: upload the file(s) to the S3 data source bucket, then manually trigger a Knowledge Base ingestion job. The system MUST NOT depend on an automated crawl or scheduled sync in v1.

#### Scenario: Documented update flow

- GIVEN the CV needs an update
- WHEN the operator follows the documented procedure
- THEN they upload the new PDF to S3 and manually start an ingestion job
- AND no code change or deploy is required

### Requirement: Embeddings and Vector Store

The system MUST use Amazon Titan Text Embeddings V2 to embed ingested content and MUST store the resulting vectors in an S3 Vectors store.

#### Scenario: Ingestion produces embeddings

- GIVEN a manual ingestion job runs
- WHEN content is chunked and embedded
- THEN the embeddings are produced by Titan Text Embeddings V2 and persisted to the S3 Vectors store

### Requirement: Grounded Retrieval

For questions answerable from the ingested content, the system MUST retrieve relevant chunks and ground the answer in them, rather than relying on the model's general knowledge.

#### Scenario: In-scope question answered from content

- GIVEN the CV states a specific technology used in a past role
- WHEN a visitor asks about that role
- THEN the retrieved chunks include the relevant CV section
- AND the answer reflects that content

### Requirement: Out-of-Scope Refusal

For questions unrelated to the CV or portfolio content, the system MUST return a polite refusal in the same language as the question rather than fabricating an answer.

#### Scenario: Off-topic question in English

- GIVEN a visitor asks "What's the weather today?"
- WHEN no relevant content is retrieved
- THEN the system responds with a polite refusal in English, `language: "en"`

#### Scenario: Off-topic question in Spanish

- GIVEN a visitor asks "¿Cuál es la capital de Francia?" in Spanish
- WHEN no relevant content is retrieved
- THEN the system responds with a polite refusal in Spanish, `language: "es"`
