# portfolio-agent

A portfolio CV chatbot on Amazon Bedrock AgentCore. This replaces the legacy
[ChatBotAPI](https://github.com/Akemid/ChatBotAPI) project (FastAPI +
LangChain + OpenAI + ChromaDB) with a serverless AWS stack: API Gateway →
Lambda → AgentCore Runtime (Strands Agents) → Bedrock Knowledge Base
(S3 Vectors, Titan Embeddings V2) + Amazon Nova Micro.

## Architecture

- **API Gateway (HTTP API)** — the public `POST /v1/chat` endpoint; owns CORS
  and stage-level throttling.
- **Lambda** — validates the request, decides the session (HttpOnly cookie),
  enforces per-session and per-IP rate limits in DynamoDB, then invokes the
  agent.
- **AgentCore Runtime** — hosts a fresh Strands `Agent` per invocation
  (direct code deployment, no container), backed by Amazon Nova Micro.
- **Bedrock Knowledge Base** — S3 Vectors storage, Titan Text Embeddings V2,
  one read-only retrieval tool the agent can call.
- **DynamoDB** — single table for session records and rate-limit counters,
  TTL-expired automatically.

See `openspec/changes/agentcore-migration-v1/design.md` for the full design,
including the threat model and cost breakdown.

## Repository layout

```
src/api/       # Lambda: domain, ports, adapters, HTTP layer
src/agent/     # AgentCore Runtime: prompt, agent factory, entrypoint
src/shared/    # Constants shared between infra/ and scripts/
infra/         # AWS CDK v2 (Python) stacks: data, agent, api
scripts/       # Build, content sync, and packaging scripts
tests/         # unit, contract, and smoke tests
docs/runbooks/ # Operational runbooks (deploy, content sync, rollback, cost)
openspec/      # Spec-driven development trail for this change
```

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync --all-groups
```

### Run the gate

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src infra
uv run pytest --cov=src --cov-fail-under=85
npx --yes aws-cdk@2 synth --all --no-lookups
```

CI (`.github/workflows/ci.yml`) runs all of the above plus a secret scan
(gitleaks), a dependency scan (pip-audit), and a content-guard check that
blocks personal content from ever entering the diff.

## Deploy

Not a single command — see [`docs/runbooks/deploy.md`](docs/runbooks/deploy.md)
for the full sequence (build the zips, deploy the three stacks in order,
one-time ACM/DNS setup, first content sync, smoke test). Also see:

- [`docs/runbooks/content-sync.md`](docs/runbooks/content-sync.md) — adding
  or updating CV/portfolio content
- [`docs/runbooks/rollback.md`](docs/runbooks/rollback.md) — reverting the
  widget or tearing down the stack
- [`docs/runbooks/cost.md`](docs/runbooks/cost.md) — the cost estimate and
  the budget alarm setup

## Security posture

- **No login**, by design — the endpoint is public. A session cookie
  (HttpOnly, Secure, SameSite=Lax) plus two independent rate-limit layers
  (per-session daily cap, per-IP per-minute cap) are the actual security
  gate, not an API Gateway authorizer.
- **Least-privilege IAM everywhere** — every role in `infra/stacks/*.py` is
  scoped to exact resource ARNs and exact actions; no `Resource: "*"` and no
  wildcard actions anywhere in the stack.
- **Personal content is never committed** — CV/portfolio files live outside
  this repository (`.gitignore` blocks `content/` and `*.pdf`) and CI's
  content-guard step fails the build if a PR diff ever adds one.

## SDD trail

This migration was planned and implemented with Spec-Driven Development —
see `openspec/changes/agentcore-migration-v1/` for the proposal, specs,
design, and task breakdown.

## License

[MIT](LICENSE)
