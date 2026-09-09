# portfolio-agent

Portfolio chatbot built on Amazon Bedrock AgentCore. Migration of the legacy
[ChatBotAPI](https://github.com/Akemid/ChatBotAPI) project (FastAPI + LangChain + OpenAI +
ChromaDB) onto a serverless stack: API Gateway → Lambda → AgentCore Runtime (Strands
Agents) → Bedrock Knowledge Base (S3 Vectors, Titan Embeddings V2) + Amazon Nova Micro.

## Status

Planning is complete (see `openspec/changes/agentcore-migration-v1/`). Implementation is
in progress; this PR bootstraps the project (tooling, package skeleton, CI).

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
```

### Run tests

```bash
uv run pytest --cov=src --cov-fail-under=85
```

### Lint and format

```bash
uv run ruff check .
uv run ruff format --check .
```
