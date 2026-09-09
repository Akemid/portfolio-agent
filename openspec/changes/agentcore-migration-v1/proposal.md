# Proposal: AgentCore Migration v1

## Intent

The portfolio CV chatbot at `sergiomondragon.com` runs on a legacy FastAPI + LangChain + OpenAI stack manually deployed to a VPS. It is unmetered, untested, and stores personal content in a public repository. Rebuild it as a serverless, rate-limited, managed-RAG service on Amazon Bedrock AgentCore so that a public, no-login endpoint can stay cheap, safe, and reproducible.

## Current State

Source: legacy repo `../ChatBotAPI` (stays untouched as reference).

| Concern | Legacy reality | File |
|---------|----------------|------|
| Runtime | FastAPI app, manual gunicorn/nginx/supervisor on a VPS | `chatbotapi_app/main.py`, `config/gunicorn_start`, `config/nginx.conf`, `config/supervisorc.conf` |
| RAG | LangChain chain, Chroma persisted to local disk (`vectorstores_cv`) | `chatbotapi_app/chatbots/chatbot_cv.py` |
| Model | OpenAI `gpt-3.5-turbo` + `OpenAIEmbeddings`, single API key | `chatbotapi_app/chatbots/chatbot_cv.py:18-20` |
| Knowledge | CV PDF + live scrape of the portfolio via `WebBaseLoader` at build time | `chatbotapi_app/chatbots/chatbot_cv.py:31-41` |
| Personal content | CV PDF committed to the repo | `static/cv_python.pdf` |
| Abuse control | None. No session, no rate limit, no auth on the public endpoint | `chatbotapi_app/routers/chatbot.py:15-46` |
| Identity | CORS with `allow_credentials` but no cookie is ever issued | `chatbotapi_app/middlewares.py` |
| Tests | Zero. `test.py` is an empty file | `chatbotapi_app/test.py` |
| Errors | Broad `except Exception`, returns the exception object instead of raising | `chatbotapi_app/routers/chatbot.py:43-46` |
| IaC | None. Deployment is manual | — |

## Target State

Public visitors ask CV/project questions at `api.sergiomondragon.com` and get a short answer in the language they asked (EN or ES). Every request is attributed to a server-issued session and throttled before any token is spent. Knowledge lives in a managed Bedrock Knowledge Base fed from S3; personal content never enters the public repo. The whole stack is defined as code and covered by tests.

## Scope

### In Scope

- API Gateway HTTP API at `api.sergiomondragon.com` fronting a Python Lambda.
- Server-issued session identity: `HttpOnly; Secure; SameSite=Lax` cookie.
- Rate limiting in DynamoDB with TTL: 10 questions per session per day, 5 requests per minute per IP. `429` returned before invoking the model.
- `InvokeAgentRuntime` call over SigV4 to an AgentCore Runtime hosting a Strands Agents agent.
- Bedrock Knowledge Base: S3 data source (CV PDF + portfolio Markdown), S3 Vectors store, Titan Text Embeddings V2.
- Amazon Nova Micro as the answering model. Non-streaming JSON response.
- IaC for all of the above (AWS CDK in Python, confirmed in design).
- Strict TDD with pytest; Bedrock/AgentCore mocked in unit tests; one real post-deploy smoke test.
- CI secret scanning and dependency scanning.
- Out-of-band S3 upload procedure for personal content, documented but not committed.
- One beginner-friendly blog note per SDD phase under `../portfolio-agent-blog/` (outside the repo).

### Out of Scope (v1 non-goals, candidates for v2)

- Streaming responses.
- Conversation memory or multi-turn context.
- Any login or user account.
- AWS WAF.
- Cost dashboards or budget alarms beyond a manual budget.
- Any change to the legacy `ChatBotAPI` repo.
- Frontend widget rework beyond the `credentials: 'include'` fetch contract.

## Capabilities

### New Capabilities

- `chat-endpoint`: public HTTP contract for asking a question and receiving a non-streaming answer, including error and language behavior.
- `session-identity`: server-issued HttpOnly session cookie, its lifecycle, and how requests are attributed without a login.
- `rate-limiting`: per-session and per-IP counters, thresholds, TTL expiry, and `429` behavior before model invocation.
- `knowledge-base`: S3 data sources, ingestion/sync, embeddings, and retrieval expectations for CV and portfolio content.
- `agent-runtime`: the AgentCore-hosted Strands agent, its system prompt, tool surface, and invocation contract.
- `infrastructure`: CDK stacks, environments, DNS, IAM boundaries, and deploy/rollback procedure.

### Modified Capabilities

- None. This is a greenfield repository.

## Approach

**Chosen: Option A — API Gateway → Lambda → AgentCore Runtime → Knowledge Base + Nova Micro.**

The Lambda is a thin, testable gate. It owns everything that must happen *before* money is spent: cookie issuance, counter increments, and the `429`. It owns nothing about reasoning. The AgentCore Runtime owns the agent, so model orchestration, retrieval, and future tool use are managed rather than hand-rolled. That split keeps the security-critical code small enough to unit-test exhaustively and keeps the agent replaceable.

API Gateway on a custom subdomain is what makes the first-party cookie possible; see `../portfolio-agent-blog/session-identity-httponly-cookie.md`.

### Rejected Alternatives

| Option | Shape | Why rejected |
|--------|-------|--------------|
| B | CloudFront + Lambda Function URL | Function URLs give a weaker request/authorizer model and the CloudFront layer adds a cache we do not want in front of a per-session-counted POST. The custom-domain and cookie story is simpler on API Gateway, and CloudFront's value (edge caching, WAF) belongs to a v2 that we explicitly deferred. |
| C | Lambda → `RetrieveAndGenerate`, no AgentCore | Cheapest and fewest moving parts, but it hardcodes a single-shot RAG call. Adding tools, multi-step reasoning, or a different agent later means rewriting the Lambda. AgentCore keeps the agent as a separate deployable and is the stated learning goal of this migration. Cost delta at portfolio traffic is negligible. |

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `infra/` | New | CDK app: API Gateway, Lambda, DynamoDB, S3, Knowledge Base, AgentCore Runtime, IAM |
| `src/api/` | New | Lambda handler: cookie, rate limit, SigV4 invoke, response shaping |
| `src/agent/` | New | Strands Agents agent packaged for AgentCore Runtime |
| `tests/` | New | pytest suites; AWS clients mocked |
| `.github/workflows/` | New | Lint, tests, secret scan, dependency scan |
| `../portfolio-agent-blog/` (outside the repo) | Modified | One note per SDD phase |
| `../ChatBotAPI` | Untouched | Reference only |
| Portfolio frontend | Modified (external) | Chat widget must call the new endpoint with `credentials: 'include'` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Cost runaway on a public endpoint | Med | Rate limit enforced before invocation; Nova Micro is the cheapest tier; manual AWS budget alert; per-session daily cap of 10 |
| Prompt injection / jailbreak on a public endpoint | High | Constrained system prompt scoped to CV content; no tools with side effects in v1; input length cap; agent has no write permissions; threat model section required in design |
| AgentCore regional availability | Med | Confirm the region during design before writing CDK; Knowledge Base and AgentCore must be co-located; fallback is Option C in the same region |
| Cookie reset abuse (clear cookies, new counter) | Med | Per-IP minute limit is the second layer; accepted residual risk documented in `../portfolio-agent-blog/session-identity-httponly-cookie.md` |
| Knowledge Base re-indexing workflow is manual and easy to forget | Med | Document the S3 upload + sync procedure; design decides the sync trigger; smoke test asserts a known fact is retrievable after deploy |
| Personal content leaking into the public repo | Med | `.gitignore` entries for content paths; CI secret scanning; content uploaded to S3 out-of-band; blocking security review before every PR |
| AWS cost of always-on components | Low | DynamoDB on-demand, S3 Vectors, and Lambda are pay-per-use; AgentCore Runtime idle cost must be confirmed in design |

## Rollback Plan

1. The legacy VPS deployment stays running and untouched throughout the migration. It is the rollback target.
2. DNS cutover is the point of no return: `api.sergiomondragon.com` is a new record, so the frontend can be pointed back to the legacy endpoint with a single frontend config change.
3. Infrastructure rollback is `cdk destroy` per stack, in reverse dependency order. Stacks are split so that the data stack (S3 + DynamoDB + Knowledge Base) can survive an application-stack rollback.
4. S3 content and DynamoDB tables carry `RETAIN` removal policies so a destroy does not delete knowledge or counters.
5. No data migration from the legacy SQLite database is performed, so there is nothing to reverse.

## Dependencies

- AWS account with Bedrock model access enabled for Amazon Nova Micro and Titan Text Embeddings V2.
- Bedrock AgentCore Runtime available in the chosen region.
- DNS control over `sergiomondragon.com` to create the `api` CNAME/alias.
- ACM certificate for `api.sergiomondragon.com`.
- CV PDF and portfolio Markdown available for out-of-band S3 upload.
- Portfolio frontend change to call the new endpoint with credentials.

## Success Criteria

- [ ] A question sent to `api.sergiomondragon.com` returns a grounded answer in the same language as the question (EN or ES).
- [ ] p95 end-to-end latency under 3.5 s for warm-session requests and under 10 s for the first request of a session (AgentCore cold start), both measured separately by the smoke test. The widget shows a "waking up" hint on the first question of a session.
- [ ] The 11th question in a session returns `429` with no Bedrock invocation recorded.
- [ ] The 6th request within 60 s from one IP returns `429` with no Bedrock invocation recorded.
- [ ] Rate-limit counters expire via DynamoDB TTL without manual cleanup.
- [ ] Estimated monthly AWS cost under USD 10 at expected portfolio traffic, with the estimate documented in design.
- [ ] `uv run pytest` passes with ≥ 85 % line coverage on `src/`, with all AWS calls mocked.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] CI secret scan and dependency scan pass with no findings.
- [ ] No personal content file exists anywhere in the repository history of `portfolio-agent`.
- [ ] A security review with no unresolved CRITICAL or HIGH findings precedes every merged PR.
- [ ] The whole stack can be recreated from scratch in a clean account via CDK, with only the content upload done manually.

## Open Questions for Design

1. **Region and model IDs.** Which region hosts AgentCore Runtime, Knowledge Base, and Bedrock together? What are the exact model IDs for Nova Micro and Titan Text Embeddings V2 there?
2. **IaC tooling.** AWS CDK in Python for everything, or CDK for the AWS resources plus the AgentCore starter toolkit for the runtime? What does the starter toolkit create that CDK cannot, and does mixing them create drift?
3. **DNS provider.** Where is `sergiomondragon.com` hosted, and does the `api` record become a Route 53 alias or a CNAME at an external provider? This also determines how the ACM certificate is validated.
4. **Knowledge Base sync trigger.** Is ingestion started manually, by an S3 event, or on a schedule? What happens to in-flight questions during a re-index?
5. **AgentCore idle cost.** Does the runtime bill while idle? This decides whether Option C stays a live fallback.
6. **Response contract.** Does the API return a single answer string or the legacy paragraph-list shape? The frontend widget depends on this.
7. **Cold start budget.** Does the Lambda need provisioned concurrency to meet the p95 target, and what does that cost?

## Owner Confirmations (2026-09-07)

- **Latency target.** Revised after design (2026-09-08): p95 < 3.5 s for warm-session requests, p95 < 10 s for the first request of a session. Warm-up pings rejected for cost reasons.
- **Cost ceiling.** USD 10/month confirmed.
- **Coverage gate.** 85 % line coverage on `src/`, enforced as a blocking check in CI. `openspec/config.yaml` updated accordingly.
- **Response shape.** v1 returns a single-answer object (`answer`, `language`), not the legacy paragraph list. The portfolio widget adapts in the same change that adds `credentials: 'include'`.
- **Knowledge freshness.** Manual content updates (upload to S3, then Knowledge Base sync) replace the legacy build-time site crawl. Accepted.
