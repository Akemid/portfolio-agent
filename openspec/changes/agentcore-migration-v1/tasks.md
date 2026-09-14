# Tasks: AgentCore Migration v1

Strict TDD is active (`openspec/config.yaml`). Every implementation task lists the
failing test to write first (RED), then the implementation step (GREEN). Test runner:
`uv run pytest`. Coverage gate: `uv run pytest --cov=src --cov-fail-under=85`.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3,740 total across 9 in-repo PRs (range 230-520 per PR) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR1 -> PR2 -> PR3 -> PR4 -> PR5 -> PR6 -> PR7 -> PR8 -> PR9 (PR6/PR7 can run in parallel with PR2-5; see dependency notes) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — orchestrator must ask the owner (stacked-to-main vs feature-branch-chain) before `sdd-apply` starts |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High
```

Individually over budget even after slicing: PR2 (~520), PR4 (~470), PR8 (~470), PR9
(~450). These should be watched during `sdd-apply` and split into a further `a`/`b`
sub-PR if the real diff exceeds ~450 lines; the split points are noted inline below.

### Suggested Work Units

| PR | Goal | Est. lines | Depends on | Base (per chain strategy) | Rollback boundary |
|----|------|-----------|------------|---------------------------|--------------------|
| 1 | Bootstrap + CI | ~230 | — | main | Revert commit; no infra exists yet |
| 2 | Domain core: models, errors, hashing, ports, fakes, session identity, cookie | ~520 | PR1 | main / tracker | Revert commit; pure Python, no deployed state |
| 3 | DynamoDB adapters: session store, rate limiter (both caps) | ~430 | PR2 | main / PR2 branch | Revert commit; no table exists until PR7 deploys |
| 4 | Lambda HTTP layer + usecase wiring (config, parser, responder, observability, usecase) | ~470 | PR2 | main / PR2 branch | Revert commit |
| 5 | AgentCore adapter + handler composition root + contract test | ~420 | PR3, PR4 | main / PR4 branch | Revert commit |
| 6 | Strands agent (prompt, language, factory, entrypoint) | ~380 | PR1 (independent of PR2-5) | main / tracker | Revert commit; no runtime deployed yet |
| 7 | CDK DataStack (S3 content + vectors, DynamoDB table, Knowledge Base, KB role) | ~370 | PR1 (independent of PR2-6) | main / tracker | `cdk destroy DataStack`; RETAIN keeps S3/DynamoDB data |
| 8 | CDK AgentStack + ApiStack (runtime, Lambda, API GW, custom domain/ACM) | ~470 | PR5, PR6, PR7 | main / PR7 branch | `cdk destroy ApiStack AgentStack` in that order |
| 9 | Scripts (upload/sync), smoke test, runbooks, README | ~450 | PR7, PR8 | main / PR8 branch | Revert commit; no deployed resource depends on scripts |
| 10 (external) | Portfolio widget: `POST /v1/chat`, `credentials:'include'`, `{answer,language}`, "waking up" hint | n/a — different repo | PR8 deployed | n/a | Point widget back at legacy endpoint |

## Phase 1: Bootstrap and CI (PR 1)

- [x] 1.1 `pyproject.toml` + `uv.lock` + `.python-version` — Python 3.12, deps: `boto3`
      (pinned), `aws-cdk-lib` (pinned), `strands-agents`, `bedrock-agentcore`; dev deps:
      `pytest`, `pytest-cov`, `ruff`, `mypy` (optional), `moto` or `botocore` stub tools.
      `[tool.pytest.ini_options] pythonpath = ["src"]`. No RED (config-only).
      Acceptance: `infrastructure` — *Infrastructure as Code* precondition; `uv run
      pytest` exits 0 with zero tests. Est: ~55 lines.
- [x] 1.2 Package skeleton — `src/api/{domain,ports,adapters,http}/__init__.py`,
      `src/agent/__init__.py`, `infra/__init__.py`, `tests/{unit,contract,smoke}/__init__.py`,
      `scripts/.gitkeep`. No RED (structure-only). Acceptance: modules importable.
      Est: ~20 lines.
- [x] 1.3 Extend `.gitignore` with Python patterns (`__pycache__/`, `.venv/`, `.env`,
      `build/`, `.pytest_cache/`, `.coverage`, `htmlcov/`) and content paths (`content/`,
      `*.pdf`), keeping the existing `docs/blog/` and `.atl/` lines.
      RED: `tests/unit/test_repo_hygiene.py::test_gitignore_covers_content_and_pdfs` —
      reads `.gitignore`, asserts it contains `content/` and `*.pdf`.
      GREEN: add the lines. Acceptance: `knowledge-base` — *Content Never Committed*,
      scenario *Gitignored content path*. Est: ~15 lines.
- [x] 1.4 CI workflow `.github/workflows/ci.yml` — jobs: `ruff check .`, `ruff format
      --check .`, `pytest --cov=src --cov-fail-under=85`, `gitleaks detect --no-git -v`,
      `pip-audit`, content-guard, `cdk synth --all`.
      RED: `tests/unit/test_ci_workflow.py::test_ci_yaml_has_required_jobs` — parses the
      YAML and asserts each required command string is present.
      GREEN: write the workflow. Acceptance: `infrastructure` — *CI Quality Gates*
      (both scenarios). Est: ~95 lines.
- [x] 1.5 Content-guard check — `scripts/check_no_content.sh`, invoked as a CI step,
      fails if the PR diff adds a path under `content/`.
      RED: `tests/unit/test_content_guard.py::test_content_guard_fails_on_content_diff` —
      feeds a fixture diff containing `content/cv/x.pdf`, asserts non-zero exit.
      GREEN: implement the grep-based check. Acceptance: `knowledge-base` — *Content
      Never Committed*, scenario *CI content check*. Est: ~35 lines.
- [ ] 1.G **Gate**: fresh-context `security-review` (CRITICAL/HIGH blocking) + code
      review before merging PR 1. Low risk (config-only diff).

## Phase 2: Domain Core — models, hashing, ports, session identity, cookie (PR 2)

- [x] 2.1 Domain models — `src/api/domain/models.py`: `ChatRequest` (validates
      non-empty, <=500 chars in `__post_init__`), `Session`, `RateLimitDecision`,
      `AgentAnswer` (frozen dataclasses).
      RED: `tests/unit/domain/test_models.py::test_chat_request_rejects_empty_message`,
      `::test_chat_request_rejects_message_over_500_chars`.
      GREEN: implement the dataclasses and validation.
      Acceptance: `chat-endpoint` — *Request Contract*, scenarios *Empty message*,
      *Message exceeds length cap*. Est: ~90 lines.
- [x] 2.2 Domain errors — `src/api/domain/errors.py`: `ValidationError`,
      `RateLimited(scope, retry_after)`, `UpstreamError`, `UpstreamTimeout`.
      RED: `tests/unit/domain/test_errors.py::test_rate_limited_carries_scope_and_retry_after`.
      GREEN: implement the exception hierarchy.
      Acceptance: `chat-endpoint` — *Upstream Failure Mapping*, *Rate-Limit Surfacing*.
      Est: ~40 lines.
- [x] 2.3 Hashing utility — `src/api/domain/hashing.py`: `derive_key(prefix: str, value:
      str) -> str` = `sha256(f"{prefix}:{value}").hexdigest()`, reused for `db:`, `rt:`,
      `log:` (sliced to 16), `ip:` per design §4.1 (no salt needed — session id already
      has >=128 bits of entropy).
      RED: `tests/unit/domain/test_hashing.py::test_derive_key_is_domain_separated` —
      asserts `derive_key("db", "x") != derive_key("rt", "x")`; `::test_full_sha256_is_64_hex_chars`.
      GREEN: implement. Acceptance: design §4.1 Identity derivation table.
      Est: ~45 lines.
- [x] 2.4 Ports — `src/api/ports/{session_store,rate_limiter,agent_client,clock,ids}.py`
      as `typing.Protocol` classes.
      RED: `tests/unit/ports/test_ports_are_protocols.py` — asserts each port class is a
      `Protocol` with the exact method signatures from design §4.
      GREEN: define the Protocols. Acceptance: design §4 Lambda Module Design.
      Est: ~55 lines.
- [x] 2.5 Test fakes — `tests/fakes/{fake_session_store,fake_rate_limiter,
      fake_agent_client,frozen_clock}.py`, in-memory implementations of the ports above.
      No RED (test infrastructure; exercised by 2.6 and Phase 4 tests).
      Acceptance: design §10 *Unit — use case* testing layer. Est: ~85 lines.
- [x] 2.6 Session identity domain logic — `src/api/domain/session_identity.py`:
      `decide_session(cookie_value, store, clock) -> (Session, is_new: bool)`.
      RED: `tests/unit/domain/test_session_identity.py::`
      `test_no_cookie_creates_new_session`,
      `test_valid_unexpired_cookie_reuses_session_without_new_cookie`,
      `test_unknown_cookie_silently_issues_fresh_session` (tamper handling),
      `test_session_25_hours_old_is_treated_as_expired`.
      GREEN: implement using `FakeSessionStore` + `FrozenClock` + `derive_key("db", ...)`.
      Acceptance: `session-identity` — *Cookie Issuance*, *Tamper and Unknown-ID
      Handling*, *Fixed Session TTL*. Est: ~165 lines.
- [x] 2.7 Cookie attribute builder — `src/api/http/cookies.py`:
      `build_set_cookie_header(session_id) -> str`.
      RED: `tests/unit/http/test_cookies.py::test_cookie_has_all_required_flags` —
      asserts `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, and a fixed 24 h `Max-Age`.
      GREEN: implement. Acceptance: `session-identity` — *Cookie Attributes*.
      Est: ~40 lines.
- [x] 2.R **Refactor**: extract any duplicated key-derivation calls in 2.6 into calls to
      `derive_key` from 2.3; no behavior change, covered by existing tests. Satisfied by
      construction: `decide_session`'s GREEN implementation already calls `derive_key("db",
      ...)` from 2.3, so there was no duplicated hashing logic to extract. The session-id
      shape-validation regex was still duplicated between 2.6 and 2.7's GREEN steps; extracted
      to `session_identity.is_valid_session_id_shape` and imported by `http/cookies.py`.
- [ ] 2.G **Gate**: fresh-context `security-review` + code review before merging PR 2
      (session/cookie logic — high sensitivity). If the real diff exceeds ~450 lines,
      split into PR2a (models/errors/hashing/ports/fakes) and PR2b (session identity +
      cookie) before requesting review.

## Phase 3: DynamoDB Adapters (PR 3)

> **Split note (apply batch, 2026-09-09):** the 3.1 diff alone is 308 changed
> lines (10 files: adapter + table/client wiring + shared moto schema
> helper). Adding 3.2+3.3 would push the PR past the ~400-line review
> budget, so this batch stops after 3.1 as **PR3a** (`feat/dynamodb-adapters`,
> branched from `feat/domain-core-b`). 3.2 and 3.3 become **PR3b**, stacked
> on top of PR3a, in the next apply batch.

- [x] 3.1 Session store adapter — `src/api/adapters/dynamo_session_store.py`: `get`,
      `create` against table `portfolio-agent-sessions` (`pk=SESSION#<derive_key("db",sid)>`,
      `sk=META`, TTL = issued + 24 h fixed).
      RED: `tests/unit/adapters/test_dynamo_session_store.py::test_get_returns_none_for_unknown_key`,
      `::test_create_writes_ttl_24h_from_issuance` (stubbed boto3 via `botocore.stub.Stubber`
      or `moto`).
      GREEN: implement. Acceptance: `session-identity` — *Fixed Session TTL*, *No PII in
      Session Records*. Est: ~140 lines.
      Also closes the PR2b apply-progress "Carried" item: production `Clock`
      (`src/api/adapters/system_clock.py`, `datetime.now(UTC)`) and `Ids`
      (`src/api/adapters/secure_ids.py`, `secrets.token_urlsafe(32)`) adapters,
      both required by the session store's constructor.
- [x] 3.2 Rate limiter — session daily cap — `src/api/adapters/dynamo_rate_limiter.py`
      (part 1): atomic `UpdateItem` with `ConditionExpression: attribute_not_exists(#c)
      OR #c < :limit` on `SESSION#<hash>/DAY#<utc-date>`, TTL = next 00:00 UTC + 300 s.
      RED: `tests/unit/adapters/test_dynamo_rate_limiter.py::test_session_cap_allows_9th_and_increments_to_10`,
      `::test_session_cap_rejects_11th_without_incrementing` (asserts
      `ConditionalCheckFailedException` maps to `RateLimited(scope="session")` and no
      write occurred).
      GREEN: implement. Acceptance: `rate-limiting` — *Per-Session Daily Cap*, *Counter
      Storage with TTL*. Est: ~150 lines.
- [x] 3.3 Rate limiter — IP sliding window — `src/api/adapters/dynamo_rate_limiter.py`
      (part 2): weighted estimate `prev_count * (1 - elapsed_fraction) + curr_count` on
      `IP#<hash>/MIN#<bucket>`, TTL = bucket start + 180 s.
      RED: `::test_ip_cap_allows_4th_request_in_window`,
      `::test_ip_cap_rejects_6th_request_without_incrementing`,
      `::test_fixed_minute_boundary_burst_is_still_blocked` (regression test for the
      rejected fixed-window design in design §4.2).
      GREEN: implement. Acceptance: `rate-limiting` — *Per-IP Per-Minute Cap*, scenario
      *Header spoofing attempt* is verified at the HTTP layer (Phase 4), not here.
      Est: ~140 lines.
- [ ] 3.G **Gate**: fresh-context `security-review` + code review before merging PR 3
      (DynamoDB conditional-write correctness under concurrency is the core safety
      property — review must exercise the ConditionExpression logic explicitly).

## Phase 4: Lambda HTTP Layer and Use Case (PR 4)

- [ ] 4.1 Config — `src/api/config.py`: frozen `Settings.from_env()` reading
      `RUNTIME_ARN`, `TABLE_NAME`, `SESSION_DAILY_LIMIT`, `IP_MINUTE_LIMIT`,
      `CORS_ALLOWED_ORIGINS`, `AGENT_TIMEOUT_SECONDS`.
      RED: `tests/unit/test_config.py::test_settings_from_env_reads_all_required_vars`,
      `::test_missing_env_var_raises`.
      GREEN: implement. Acceptance: `rate-limiting` — thresholds *MUST be configurable
      via an environment variable*. Est: ~45 lines.
- [ ] 4.2 Request parser — `src/api/http/request_parser.py`: HTTP API v2 event ->
      `ChatRequest`, cookie value, `requestContext.http.sourceIp`.
      RED: `tests/unit/http/test_request_parser.py::test_parses_valid_body_and_cookie`,
      `::test_rejects_invalid_json`, `::test_uses_source_ip_and_ignores_x_forwarded_for`
      (forged header fixture).
      GREEN: implement against recorded API Gateway v2 event fixtures.
      Acceptance: `chat-endpoint` — *Request Contract* (*Invalid JSON*); `rate-limiting`
      — *Header spoofing attempt*. Est: ~120 lines.
- [ ] 4.3 Responder — `src/api/http/responder.py`: domain result -> status code, CORS
      headers, `Set-Cookie`, `Retry-After`.
      RED: `tests/unit/http/test_responder.py::test_success_response_shape`,
      `::test_429_includes_retry_after_and_error_body`,
      `::test_disallowed_origin_omitted_from_cors_header`,
      `::test_502_and_504_do_not_leak_exception_detail`.
      GREEN: implement. Acceptance: `chat-endpoint` — *Response Contract*, *Rate-Limit
      Surfacing*, *CORS Restriction*, *Upstream Failure Mapping*; `rate-limiting` —
      *429 Response Shape*. Est: ~130 lines.
- [ ] 4.4 Observability — `src/api/observability.py`: `hash_for_log(sid)` (uses
      `derive_key("log", sid)[:16]`), `truncate(msg, 100)`, structured JSON log emitter.
      RED: `tests/unit/test_observability.py::test_hash_for_log_never_equals_raw_id`,
      `::test_truncate_caps_at_100_chars`.
      GREEN: implement. Acceptance: `chat-endpoint` — *Log Redaction* (both scenarios).
      Est: ~55 lines.
- [ ] 4.5 Use case — `src/api/usecases/answer_question.py`: the single ordered path
      validate -> session -> rate limits -> invoke -> map, using only ports.
      RED: `tests/unit/usecases/test_answer_question.py::test_rate_limit_short_circuits_before_invoke`
      (asserts `FakeAgentClient.ask` is never called), `::test_happy_path_calls_ports_in_order`,
      `::test_upstream_error_maps_to_upstream_error_domain_type`.
      GREEN: implement using `FakeSessionStore`, `FakeRateLimiter`, `FakeAgentClient`
      from 2.5. Acceptance: `rate-limiting` — *Check-and-Increment Before Invocation*.
      Est: ~120 lines.
- [ ] 4.G **Gate**: fresh-context `security-review` + code review before merging PR 4.
      If the real diff exceeds ~450 lines, split 4.1-4.2 (parsing/config) from 4.3-4.5
      (responding/use case) into PR4a/PR4b.

## Phase 5: AgentCore Adapter, Handler, Contract (PR 5)

- [ ] 5.1 AgentCore client adapter — `src/api/adapters/agentcore_client.py`: boto3
      `client("bedrock-agentcore")`, `invoke_agent_runtime(runtimeSessionId=derive_key
      ("rt", sid) [64 chars], payload={"prompt": message, "language_hint": None})`.
      RED: `tests/unit/adapters/test_agentcore_client.py::test_runtime_session_id_is_64_hex_chars`,
      `::test_payload_uses_prompt_key`, `::test_timeout_maps_to_upstream_timeout`,
      `::test_client_error_maps_to_upstream_error` (`botocore.stub.Stubber`).
      GREEN: implement. Acceptance: `agent-runtime` — *Hosting and Invocation*,
      *Invocation Payload Contract*; design §6 runtimeSessionId 33-256 char constraint.
      Est: ~140 lines.
- [ ] 5.2 Handler composition root — `src/api/handler.py`: module-scope adapter wiring
      (reused across warm invocations), delegates to `answer_question`.
      RED: `tests/unit/test_handler.py::test_handler_wires_real_adapter_types`,
      `::test_boto3_client_construction_includes_bedrock_agentcore` (guards against the
      "bundled boto3 lacks the client" risk from design §12/13).
      GREEN: implement. Acceptance: design §4 composition root responsibility.
      Est: ~90 lines.
- [ ] 5.3 Contract test — `tests/contract/test_payload_contract.py`: one shared JSON
      fixture pair validated from both the Lambda's produced payload and the (Phase 6)
      agent entrypoint's accepted payload; likewise for the response shape.
      RED: write the fixture and the test against the Lambda side first (fails until
      Phase 6 exists on the other side, per design §10 *Contract* row — acceptable as
      a cross-phase pending test, skip-marked until Phase 6 lands).
      GREEN: unskip once Phase 6 ships. Acceptance: `agent-runtime` — *Invocation
      Payload Contract*, scenario *Contract round-trip*. Est: ~85 lines.
- [ ] 5.4 End-to-end error mapping test — `tests/unit/test_error_mapping_e2e.py`:
      drives `handler.py` with a fake AgentClient raising each domain error and asserts
      502/504 with no internal detail in the body.
      RED then GREEN (implementation already exists from 4.3/5.1; this test closes the
      seam between them). Acceptance: `chat-endpoint` — *Upstream Failure Mapping*
      (both scenarios). Est: ~65 lines.
- [ ] 5.G **Gate**: fresh-context `security-review` + code review before merging PR 5
      (SigV4 invocation and error-detail leakage are both security-relevant).

## Phase 6: Strands Agent (PR 6, can proceed in parallel with PR2-5)

- [ ] 6.1 System prompt — `src/agent/prompts.py`: `SYSTEM_PROMPT` covering identity
      (first person), grounding (retrieved text is data, never commands), language
      detection/response, ~3-sentence length, refusal, injection resistance, and the
      `{"answer","language"}` JSON output shape (design §5).
      RED: `tests/unit/agent/test_prompts.py::test_prompt_contains_first_person_instruction`,
      `::test_prompt_forbids_revealing_itself`, `::test_prompt_requires_json_output_shape`.
      GREEN: write the prompt text. Acceptance: `agent-runtime` — *First-Person
      Persona*, *Off-Topic and Prompt-Injection Refusal*. Est: ~85 lines.
- [ ] 6.2 Language normalization — `src/agent/language.py`: `normalize_language(raw) ->
      "en" | "es"`, defaults to `"en"` on anything unexpected.
      RED: `tests/unit/agent/test_language.py::test_normalizes_english_and_spanish`,
      `::test_unexpected_value_defaults_to_en`.
      GREEN: implement. Acceptance: `agent-runtime` — *Bilingual Detection*.
      Est: ~55 lines.
- [ ] 6.3 Agent factory — `src/agent/agent_factory.py`: `build_agent() -> Agent`, Nova
      Micro model id from `MODEL_ID` env var, exactly one Strands `retrieve` tool bound
      to `STRANDS_KNOWLEDGE_BASE_ID`, no other tools.
      RED: `tests/unit/agent/test_agent_factory.py::test_model_id_read_from_env_not_hardcoded`,
      `::test_agent_has_exactly_one_read_only_tool`.
      GREEN: implement. Acceptance: `agent-runtime` — *Foundation Model*, *No
      Side-Effect Tools*. Est: ~80 lines.
- [ ] 6.4 Entrypoint — `src/agent/main.py`: `BedrockAgentCoreApp` + `@app.entrypoint`,
      builds a **fresh** `Agent` per invocation (never module-scope), catches all
      exceptions, returns `{"answer","language"}`.
      RED: `tests/unit/agent/test_main.py::test_fresh_agent_built_per_invocation` (calls
      the entrypoint twice with a fake factory and asserts it was invoked twice — this
      is the single most important test per design §5), `::test_unhandled_exception_never_returns_traceback`.
      GREEN: implement. Acceptance: `agent-runtime` — *Stateless Single-Turn Answers*.
      Est: ~130 lines.
- [ ] 6.5 Unskip the Phase 5 contract test (5.3) now that the agent side exists.
- [ ] 6.G **Gate**: fresh-context `security-review` + code review before merging PR 6
      (prompt-injection resistance is the primary review focus).

## Phase 7: CDK DataStack (PR 7, can proceed in parallel with PR2-6)

- [ ] 7.1 `infra/app.py` + `cdk.json` — app skeleton declaring stack instantiation order
      `DataStack -> AgentStack -> ApiStack` (Agent/Api added in Phase 8), explicit
      `env={account, region: "us-east-1"}`.
      RED: `tests/unit/infra/test_app_synth.py::test_app_synth_succeeds` (fails until
      7.2 exists — acceptable bootstrap-then-fill pattern for infra tasks).
      GREEN: minimal app that synths with only DataStack. Est: ~40 lines.
- [ ] 7.2 `infra/stacks/data_stack.py` — S3 content bucket (`RemovalPolicy.RETAIN`,
      Block Public Access), S3 vector bucket + `CfnIndex` (dimension=1024), DynamoDB
      table `portfolio-agent-sessions` (`pk`, `sk`, TTL attribute `ttl`, on-demand,
      `RemovalPolicy.RETAIN`), `CfnKnowledgeBase` (S3_VECTORS storage config, Titan V2
      embedding model `amazon.titan-embed-text-v2:0`), S3 data source, KB service role
      (`bedrock:InvokeModel` Titan ARN only, `s3:GetObject`+`ListBucket` content bucket
      only, `s3vectors:*Vectors|GetIndex` scoped to the one index ARN).
      RED: `tests/unit/infra/test_data_stack.py::test_table_has_ttl_enabled`,
      `::test_bucket_and_table_removal_policy_is_retain`,
      `::test_kb_role_has_no_wildcard_resource` (`aws_cdk.assertions.Template`).
      GREEN: implement the stack. Acceptance: `infrastructure` — *Data Retention on
      Destroy*; `knowledge-base` — *S3 Data Source Layout*, *Embeddings and Vector
      Store*; design §8 RQ-1 (1024 dims), §9.2 KB role. Est: ~220 lines.
- [ ] 7.3 Additional synth assertions — no other role in the stack grants a wildcard
      resource; content bucket path prefixes match `content/cv/` and
      `content/portfolio/{en,es}/`. Folded into 7.2's test file for cohesion.
      Est: included above.
- [ ] 7.G **Gate**: fresh-context `security-review` + code review before merging PR 7
      (least-privilege IAM on the KB role is the focus).

## Phase 8: CDK AgentStack + ApiStack (PR 8)

- [ ] 8.1 `scripts/build_agent.sh` — `uv pip install --target build/agent` + copy
      `src/agent`, producing the input directory for the CDK `Asset`.
      No RED (shell packaging step). GREEN: write the script.
      Acceptance: design §RQ-2 packaging flow. Est: ~20 lines.
- [ ] 8.2 `infra/stacks/agent_stack.py` — `aws_s3_assets.Asset(path="build/agent")`,
      `CfnRuntime` with `codeConfiguration` (`runtime="PYTHON_3_12"`, no container/ECR),
      agent execution role (`bedrock:InvokeModel` on the Nova Micro FM ARN **and** the
      `us.amazon.nova-micro-v1:0` inference-profile ARN, `bedrock:Retrieve` on the one
      KB ARN, `s3:GetObject` on the code asset only), trust policy with
      `aws:SourceAccount` and `aws:SourceArn` conditions per design §9.2.
      RED: `tests/unit/infra/test_agent_stack.py::test_runtime_uses_code_configuration_not_container`,
      `::test_agent_role_grants_both_model_arns`, `::test_agent_role_has_no_wildcard_resource`.
      GREEN: implement. Acceptance: `infrastructure` — *Least-Privilege Agent Role*;
      `agent-runtime` — *Foundation Model*; design D3, D2. Est: ~155 lines.
- [ ] 8.3 `infra/stacks/api_stack.py` — Lambda (Python 3.12, 512 MB) with least-privilege
      role (`bedrock-agentcore:InvokeAgentRuntime` on one runtime ARN, `dynamodb:GetItem|
      Query|PutItem|UpdateItem` on one table ARN, no wildcard), HTTP API `POST /v1/chat`,
      custom domain `api.sergiomondragon.com` + ACM cert requested in `us-east-1` (DNS
      validation CNAME to be added manually at DigitalOcean — documented, not automated),
      CORS allowlist from `Settings`.
      RED: `tests/unit/infra/test_api_stack.py::test_lambda_role_has_exactly_one_runtime_and_table_arn`,
      `::test_lambda_role_has_no_wildcard_resource`, `::test_custom_domain_configured_with_acm`.
      GREEN: implement. Acceptance: `infrastructure` — *Least-Privilege Lambda Role*,
      *Custom Domain*; design §9.2 Lambda role JSON, §11 DNS runbook.
      Est: ~180 lines.
- [ ] 8.4 Wire `infra/app.py` to instantiate all three stacks in dependency order with
      explicit `add_dependency` calls; extend `test_app_synth.py` to assert `cdk synth
      --all` succeeds end to end.
      Acceptance: `infrastructure` — *Infrastructure as Code*, scenario *Full stack
      recreation*. Est: ~35 lines.
- [ ] 8.5 Document the one-time manual DNS step (ACM CNAME validation, then the
      `api.sergiomondragon.com` CNAME at DigitalOcean) inline as a code comment on the
      certificate construct, pointing to the Phase 9 runbook.
- [ ] 8.G **Gate**: fresh-context `security-review` + code review before merging PR 8.
      If the real diff exceeds ~450 lines, split 8.1-8.2 (AgentStack) from 8.3-8.4
      (ApiStack) into PR8a/PR8b.

## Phase 9: Scripts, Smoke Test, Runbooks, README (PR 9)

- [ ] 9.1 `scripts/upload_content.sh <file>` — uploads to `content/cv/` or
      `content/portfolio/{en,es}/` in the content S3 bucket; the file never touches git.
      No RED (operational script). Acceptance: `knowledge-base` — *Manual Sync
      Procedure*. Est: ~30 lines.
- [ ] 9.2 `scripts/sync_kb.py` — calls `StartIngestionJob`, polls to a terminal state,
      exits non-zero on failure.
      RED: `tests/unit/scripts/test_sync_kb.py::test_polls_until_terminal_state`
      (stubbed boto3), `::test_exits_nonzero_on_failed_status`.
      GREEN: implement. Acceptance: `knowledge-base` — *Manual Sync Procedure*.
      Est: ~95 lines.
- [ ] 9.3 `tests/smoke/test_smoke.py` (never runs in CI, never counted toward coverage):
      real `POST /v1/chat` against a deployed stack asserts a known CV fact is
      retrievable, the `Set-Cookie` header carries all four flags, the 11th question in
      a session returns 429, and **warm p95 and cold first-call latency are reported as
      two separate numbers**.
      Acceptance: proposal *Success Criteria* (latency split); `chat-endpoint` —
      *End-to-End Latency Budget*; `architecture/latency-slo` decision (warm < 3.5 s,
      first-of-session < 10 s). Est: ~135 lines.
- [ ] 9.4 Runbooks — `docs/runbooks/content-update.md` (upload -> sync -> smoke test;
      do not sync while demonstrating the bot), `docs/runbooks/rollback.md` (frontend
      config revert; `cdk destroy` in reverse order `ApiStack -> AgentStack ->
      DataStack`; RETAIN data survives), `docs/runbooks/dns-and-certificate.md`
      (one-time manual DigitalOcean CNAME steps). Est: ~145 lines.
- [ ] 9.5 `README.md` — project overview, prerequisites, `uv sync`, test/lint/coverage
      commands, `cdk deploy` order, links to the runbooks and to
      `../portfolio-agent-blog/`. Est: ~60 lines.
- [ ] 9.G **Gate**: fresh-context `security-review` + code review before merging PR 9.

## Phase 10 (External — not in this repository)

- [ ] 10.1 **External**: Portfolio frontend widget change. Update the chat widget to
      call `POST https://api.sergiomondragon.com/v1/chat` with `fetch(url, {credentials:
      'include'})`, adapt to the new `{answer, language}` response shape (drop the
      legacy paragraph-list rendering), and show a "waking up" hint on the first
      question of a session (informed by the split latency SLO — first-request p95 up
      to 10 s). Depends on PR 8 being deployed. Tracked here for completeness only; no
      task in this repo implements it.

## Traceability Note

The `agent-runtime` spec's *Contract round-trip* scenario already reads `{"prompt":
...}` (spec file, line 108) — the spec/design divergence flagged in `state.yaml`
carried_risks was corrected before this phase ran. No spec edit is needed in `sdd-tasks`.
