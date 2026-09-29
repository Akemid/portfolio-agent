# Verification Report: agentcore-migration-v1

**Change**: agentcore-migration-v1
**Verified against**: `feat/ops-docs` (tip of the stacked PR chain; HEAD at verification time includes PR0–PR9b work)
**Mode**: Strict TDD
**Date**: 2026-09-21

## Executive Summary

All code-level gates pass on the current tip (408 tests, 99.87% coverage, ruff/format/mypy clean,
`cdk synth --all` reproduced locally with neutralized credentials for all 3 stacks, zero personal
content in 50 commits of history). Every spec requirement has a corresponding implementation and a
passing test; the only requirements that are only *partially* verifiable are ones that inherently
depend on live LLM behavior or a real deployment (grounding quality, refusal behavior, actual
latency, actual monthly cost) — these are correctly called out as unverifiable pre-deploy in
`design.md` and are not implementation defects.

The real gap is **delivery, not code**: only 7 of 16 opened PRs are merged to `main` (PR0–PR4a).
PR5 through PR9b (everything from the AgentCore adapter onward — including **all three CDK
stacks**) exist only as open, unreviewed PRs stacked on each other's branches, not on `main`. Four
of those open PRs (PR7 #12, PR8a #13, PR8b #14, PR9a #15) currently show a **failing** GitHub CI
check, caused by a stale pre-fix version of `ci.yml` that predates the `npx aws-cdk@2` synth fix —
not a real code defect (I reproduced the current synth successfully), but each of those branches
needs to be rebased/updated and CI re-run before merge. No PR, merged or open, has a GitHub-native
review recorded (0 reviews, 0 comments across all 16 PRs) — the documented "fresh-context
security-review + code review" gates were run out-of-band and their findings/fixes are narrated in
`apply-progress`, but there is no independently-checkable GitHub artifact for most of them.

**Verdict: PASS WITH WARNINGS** for everything implemented so far; **NOT READY FOR ARCHIVE** —
significant real work (merging PR5–PR9b, task 9.G's final gate, the external widget change, and a
first real deploy) remains before this change is done.

**CRITICAL**: 0 · **WARNING**: 7 · **SUGGESTION**: 4

---

## Per-Spec Compliance Matrix

Legend: ✅ MET (implemented + covering test passes) · ⚠️ PARTIAL (implemented and unit-tested, but
full compliance is only checkable against a live model/deployment) · ❌ NOT MET.

### `chat-endpoint`

| Requirement | Status | Evidence |
|---|---|---|
| Endpoint Shape | ✅ | `infra/stacks/api_stack.py` defines exactly one route (`POST /v1/chat`); `tests/unit/infra/test_api_stack.py`. "Wrong method → 404" (corrected from the original 405, HTTP API v2 has no per-method 405) is native API Gateway behavior, documented in `api_stack.py`'s docstring and the spec text itself (line 23) — not independently unit-tested (nothing to unit-test; it's an AWS platform default) |
| Request Contract | ✅ | `src/api/domain/models.py::ChatRequest`, `src/api/http/request_parser.py::extract_message`; `tests/unit/domain/test_models.py`, `tests/unit/http/test_request_parser.py`, `tests/unit/test_handler.py` (400 paths) |
| Response Contract | ✅ | `src/api/http/responder.py::success_response`; `tests/unit/http/test_responder.py::test_success_response_shape` |
| Rate-Limit Surfacing | ✅ | `responder.py` 429 mapping + `Retry-After`; `usecases/answer_question.py` short-circuit; `test_answer_question.py::test_rate_limit_short_circuits_before_invoke` asserts `FakeAgentClient.ask` never called |
| Upstream Failure Mapping | ✅ | `responder.py` 502/504, generic message; `test_responder.py::test_502_and_504_do_not_leak_exception_detail`, `test_error_mapping_e2e.py` |
| CORS Restriction | ✅ | Configured on the `HttpApi` itself (API Gateway ignores backend CORS headers otherwise — verified against AWS docs, documented in `api_stack.py`); `test_api_stack.py` CORS assertions + `responder.py`'s own (now-dead-on-the-wire, intentionally kept) CORS headers unit-tested |
| Log Redaction | ✅ | `src/api/observability.py::hash_for_log/truncate`; `tests/unit/test_observability.py` |
| End-to-End Latency Budget | ⚠️ | `tests/smoke/test_smoke.py` genuinely measures and reports warm vs. cold-first-request p95 as two separate numbers (fixed a real bug where N<20 samples silently degraded nearest-rank p95 to `max()` — `_percentile_label`); its pure logic is unit-tested in `test_smoke_script.py` (19 tests). Actual latency numbers do not exist yet — nothing is deployed. Correctly carried as an open risk in `design.md` §13 |

### `session-identity`

| Requirement | Status | Evidence |
|---|---|---|
| Cookie Issuance | ✅ | `domain/session_identity.py::decide_session`; `tests/unit/domain/test_session_identity.py` (4 tests incl. first-time/returning) |
| Cookie Attributes | ✅ | `http/cookies.py::build_set_cookie_header`; `tests/unit/http/test_cookies.py::test_cookie_has_all_required_flags` |
| Session Identifier Quality | ✅ | `adapters/secure_ids.py` (`secrets.token_urlsafe(32)`); `tests/unit/adapters/test_secure_ids.py` |
| Fixed Session TTL | ✅ | `adapters/dynamo_session_store.py` TTL = issued+24h; `test_session_identity.py::test_session_25_hours_old_is_treated_as_expired`, `test_dynamo_session_store.py::test_create_writes_ttl_24h_from_issuance` |
| Tamper and Unknown-ID Handling | ✅ | `test_session_identity.py::test_unknown_cookie_silently_issues_fresh_session` |
| No PII in Session Records | ✅ | `dynamo_session_store.py` writes only `created_at`/`ttl`; `test_dynamo_session_store.py` asserts the raw id is never stored/returned |

### `rate-limiting`

| Requirement | Status | Evidence |
|---|---|---|
| Per-Session Daily Cap | ✅ | `adapters/dynamo_rate_limiter.py`; `test_dynamo_rate_limiter.py::test_session_cap_allows_9th_and_increments_to_10` / `::test_session_cap_rejects_11th_without_incrementing` |
| Per-IP Per-Minute Cap | ✅ | Weighted sliding window (rejects the naive fixed-bucket 2× leak); `::test_ip_cap_allows_4th...`, `::test_ip_cap_rejects_6th...`, `::test_fixed_minute_boundary_burst_is_still_blocked` (regression test) |
| Header spoofing | ✅ | `http/request_parser.py` reads only `requestContext.http.sourceIp`; `test_request_parser.py::test_uses_source_ip_and_ignores_x_forwarded_for` |
| Check-and-Increment Before Invocation | ✅ | IP checked before session (`test_ip_denial_does_not_increment_the_session_counter`); use-case ordering test above |
| Counter Storage with TTL | ✅ | TTL set via `if_not_exists` in the same `UpdateItem`, written once; `test_session_ttl_is_end_of_day_plus_grace_and_set_only_once` |
| 429 Response Shape | ✅ | `test_responder.py::test_429_includes_retry_after_and_error_body` |

### `knowledge-base`

| Requirement | Status | Evidence |
|---|---|---|
| S3 Data Source Layout | ✅ | `data_stack.py` (single `content/` inclusion prefix — `AWS::Bedrock::DataSource` allows only one, verified against CDK/CFN docs — covers both `content/cv/` and `content/portfolio/{en,es}/`); `test_data_stack.py` |
| Content Never Committed | ✅ | `.gitignore` (`content/`, `*.pdf`); `scripts/check_no_content.sh` + CI content-guard step; independently verified: `git log --all --diff-filter=A --name-only` across all 50 commits has **zero** matches for `\.pdf$|(^|/)content/|\.env` |
| Manual Sync Procedure | ✅ | `scripts/sync_content.py` (merged upload+ingest, contrary to tasks.md's original two-script split — documented deviation); `tests/unit/scripts/test_sync_content.py` (16 tests) |
| Embeddings and Vector Store | ✅ | Titan V2, `CfnIndex.dimension=1024` matching KB embedding config; `test_data_stack.py::test_vector_index_dimension_and_metric_match_the_embedding_model` |
| Grounded Retrieval | ⚠️ | `agent/knowledge_base.py::build_search_tool` (own `@tool` against `bedrock-agent-runtime.Retrieve`, deviating from the community `strands-agents-tools` package for dependency-floor reasons — documented); prompt instructs grounding-only answers (`test_prompts.py`). Actual retrieval *quality* is unverifiable without a deployed KB and real content |
| Out-of-Scope Refusal | ⚠️ | Prompt instructs refusal in the question's language (`test_prompts.py`); real model refusal behavior is untestable without a live Nova Micro call — inherent to any LLM-driven spec |

### `agent-runtime`

| Requirement | Status | Evidence |
|---|---|---|
| Hosting and Invocation | ✅ | `adapters/agentcore_client.py`; `tests/unit/test_lambda_package_isolation.py` statically guards that `src/api` never imports `strands`/`bedrock_agentcore` directly |
| First-Person Persona | ⚠️ | `agent/prompts.py::SYSTEM_PROMPT`; `test_prompts.py::test_prompt_contains_first_person_instruction`. Actual model compliance unverifiable pre-deploy |
| Bilingual Detection | ✅ (normalization) / ⚠️ (detection) | `agent/language.py::normalize_language` fully tested (`test_language.py`); actual language *detection* is delegated to the model and only verifiable live |
| Answer Length | ✅ | Prompt instruction + hard backstop `MAX_ANSWER_CHARS=1200` truncated at a sentence/word boundary in `main.py`; unit-tested |
| Off-Topic and Prompt-Injection Refusal | ⚠️ | Prompt forbids revealing itself / off-persona behavior (`test_prompts.py::test_prompt_forbids_revealing_itself`); real refusal behavior unverifiable pre-deploy |
| No Side-Effect Tools | ✅ | `agent_factory.py`; `test_agent_factory.py::test_agent_has_exactly_one_read_only_tool` |
| Foundation Model | ✅ | `MODEL_ID` env var, never hardcoded; `test_agent_factory.py::test_model_id_read_from_env_not_hardcoded` |
| Stateless Single-Turn Answers | ✅ | Fresh `Agent()` built inside the entrypoint every call; `test_main.py::test_fresh_agent_built_per_invocation` — explicitly called out in design as "the single most important test" |
| Invocation Payload Contract | ✅ | `{"prompt": ...}` on both sides (spec's illustrative `{"message":...}` scenario was corrected to match — confirmed in the current spec text, line 108); `tests/contract/test_payload_contract.py` |
| Timeout Budget | ✅ | `AgentCoreClient` connect=3s/read=12s; Lambda `LAMBDA_TIMEOUT = Duration.seconds(20)` in `api_stack.py:142` (matches the PR5 apply-progress recommendation) |

### `infrastructure`

| Requirement | Status | Evidence |
|---|---|---|
| Infrastructure as Code | ✅ | All 3 stacks (`DataStack`, `AgentStack`, `ApiStack`) in CDK Python; reproduced `cdk synth --all --no-lookups` locally with neutralized credentials — succeeded, all 3 templates produced |
| Least-Privilege Lambda Role | ✅ | Exactly one runtime ARN + one table ARN, **no** `dynamodb:Query` (confirmed absent via `grep`, removed in a review-fix), no wildcard; `test_api_stack.py::test_lambda_role_has_exactly_one_runtime_and_table_arn`, `::test_lambda_role_has_no_wildcard_resource` |
| Least-Privilege Agent Role | ✅* | Agent role grants `InvokeModel` on Nova Micro only + `Retrieve` on one KB; Titan is invoked by the separate **KB service role**, not the agent role. The requirement text bundles both models under "the AgentCore Runtime's execution role," which is stricter than what's actually needed — the implemented split (agent role: Nova Micro only; KB role: Titan only) is *more* least-privilege than the literal spec text describes. See SUGGESTION below |
| No Secrets in Repository | ✅ | No long-lived credentials anywhere (IAM-role based); `gitleaks` run in CI (last observed run: "no leaks found", 85 commits scanned) |
| CI Quality Gates | ✅ (code) / ⚠️ (delivery) | `.github/workflows/ci.yml` runs ruff/format/pytest+cov85/gitleaks/pip-audit/content-guard/cdk-synth, all blocking — matches `design.md` §11 exactly. All gates reproduced clean locally on the current tip. **However**, 4 open PRs (#12 PR7, #13 PR8a, #14 PR8b, #15 PR9a) show a stale **FAILURE** on GitHub because their branches predate the `npx aws-cdk@2` synth fix (`0968263`) — see WARNING below |
| Data Retention on Destroy | ✅ | `RemovalPolicy.RETAIN` on the content bucket and the DynamoDB table; `test_data_stack.py::test_bucket_and_table_removal_policy_is_retain` |
| Custom Domain | ✅ | `api.sergiomondragon.com` + ACM cert in `us-east-1`; `test_api_stack.py::test_custom_domain_configured_with_acm`; DNS validation is manual by design, documented in `docs/runbooks/deploy.md` |
| Monthly Cost Ceiling | ✅ (documented) / ⚠️ (unverified live) | `design.md` §7: ~USD 0.65/month, ~15× headroom under the $10 ceiling. No AWS Budget exists yet — creating it is a documented manual step (`docs/runbooks/cost.md`), not a CDK resource (by design, D12) |

---

## Success Criteria Verdict (proposal.md)

| # | Criterion | Verdict | Note |
|---|---|---|---|
| 1 | Grounded answer in the question's language | ⚠️ Unverifiable pre-deploy | Prompt + retrieval tool built and unit-tested; no live model call exists to confirm |
| 2 | Split latency (warm p95<3.5s, cold p95<10s), measured separately | ⚠️ Unverifiable pre-deploy | Smoke test correctly built to measure/report both separately (fixed a real N<20 percentile-honesty bug); no deployment exists yet to run it against |
| 3 | 11th question/session → 429, no Bedrock invocation | ✅ MET | `test_session_cap_rejects_11th_without_incrementing` + use-case short-circuit test |
| 4 | 6th request/min/IP → 429, no invocation | ✅ MET | `test_ip_cap_rejects_6th_request_without_incrementing`, IP-checked-before-session ordering |
| 5 | TTL expiry, no manual cleanup | ✅ MET (at the attribute level) | TTL attributes correctly computed and set on every counter/session item; the actual TTL *sweep* is an AWS-managed background process, unverifiable without a real table |
| 6 | Est. monthly cost < USD 10 | ✅ MET (documented) | ~$0.65/month estimate in `design.md` §7; real cost only confirmable post-deploy |
| 7 | `uv run pytest` ≥ 85% coverage, AWS mocked | ✅ MET — verified live | **408 passed, 99.87% coverage** (`--cov-fail-under=85`); zero real AWS calls (moto/`botocore.stub.Stubber` only) |
| 8 | `ruff check` + `ruff format --check` pass | ✅ MET — verified live | Both clean on current tip |
| 9 | CI secret scan + dependency scan pass | ✅ MET (code) / ⚠️ (delivery) | Clean in the last real CI run observed (gitleaks: no leaks, 85 commits; pip-audit: no known vulnerabilities). 4 open PRs currently show a stale unrelated CI failure (see WARNING) |
| 10 | No personal content anywhere in repo history | ✅ MET — verified live | `git log --all --diff-filter=A --name-only` across all 50 commits: zero matches for PDF/`content/`/`.env` patterns |
| 11 | Security review, no unresolved CRITICAL/HIGH, before every merged PR | ⚠️ Only partly evidenced | Of the 7 **merged** PRs, only PR0 and PR2a carry an explicit inline "security PASS" note in `apply-progress`. PR1, PR2b, PR3a, PR3b, PR4a were merged with no explicit security-review verdict recorded anywhere, and **zero GitHub PR reviews exist on any of the 16 PRs** (merged or open) — the documented review/fix narratives in `apply-progress` are the only evidence trail, and they are not independently auditable via GitHub. See WARNING below |
| 12 | Whole stack recreatable from scratch via CDK | ⚠️ True only on the tip branch, not on `main` | `cdk synth --all` succeeds and produces all 3 stacks (verified locally). But **`main` does not contain `infra/` at all** — `DataStack`/`AgentStack`/`ApiStack` exist only on unmerged branches (PR7/PR8a/PR8b, open as #12/#13/#14). "Recreate from scratch" is not yet true of `main` |

**8/12 fully MET, 4/12 correctly PARTIAL/pending-deploy or pending-merge** (none are code defects; #12 and #11 are delivery-state gaps).

---

## Real Outstanding Work (vs. the 13 unchecked tasks.md items)

tasks.md shows 13 unchecked items: 10 merge gates (`1.G`…`9.G`, including the `8.G-a`/`8.G-b` split),
tasks `9.4` and `9.5`, and the external task `10.1`.

| Item | tasks.md state | Reality |
|---|---|---|
| **9.4** Runbooks | `[ ]` unchecked | **Done.** `docs/runbooks/{deploy,content-sync,rollback,cost}.md` all exist, committed (`35d8095`), reasonably complete, opened as part of PR9b (#16). Checkbox/state.yaml were never updated — pure bookkeeping drift |
| **9.5** README | `[ ]` unchecked | **Done.** `README.md` rewritten (`048fc82`), covers overview/prerequisites/gate commands/deploy pointer/runbook links — matches the task's own acceptance text. Checkbox drift only |
| **1.G – 7.G, 8.G-a** (7 gates) | `[ ]` unchecked | **Genuinely pending** for the still-open PRs (PR5 onward are not merged); **for the merged PRs (1.G–4.G)**, a review narrative exists in `apply-progress` but is not recorded on GitHub — see WARNING. Not fully closeable from repo evidence alone |
| **8.G-b, 9.G** | `[ ]` unchecked | Genuinely pending — PR8b (#14) and PR9a/PR9b (#15/#16) are open, unreviewed on GitHub, and PR7/#12, PR8a/#13, PR8b/#14, PR9a/#15 additionally have a stale failing CI check that must be cleared first |
| **10.1** Portfolio widget | `[ ]` unchecked | Genuinely outstanding, external repo, correctly noted as blocked on PR8/ApiStack being deployed (which itself isn't merged yet) |

**The actually-larger gap tasks.md's checkboxes don't surface**: 9 of 16 opened PRs (#9 PR5, #10 PR5b,
#11 PR6, #12 PR7, #13 PR8a, #14 PR8b, #15 PR9a, #16 PR9b) are **not merged to `main`**. That is the
real remaining work — merging the chain — not just ticking 13 checkboxes. `main` today has only
Phases 1–4a; every CDK stack, the agent, the ops scripts, and the docs exist solely on the stacked
branch chain.

---

## Consistency Check (specs ↔ design ↔ code ↔ runbooks)

Previously-flagged drifts and their current state, re-verified:

- **`Query` IAM permission** — fixed. `grep -n "Query" infra/stacks/api_stack.py` shows only explanatory comments; no `dynamodb:Query` action is granted. Matches `design.md` §9.2's corrected Lambda-role JSON.
- **405 vs. 404 scenario** — fixed. `specs/chat-endpoint/spec.md` line 23 now reads "returns 404 Not Found," matching `api_stack.py`'s actual (and only possible, per API Gateway HTTP API v2) behavior.
- **Retrieval tool** — fixed and consistent. `design.md` §5, `agent/knowledge_base.py`, and `tasks.md`'s 6.1–6.3 note all describe the same own-`@tool`-against-`Retrieve` decision, for the same reason (dependency floor).
- **Streaming default** — fixed. `agent_factory.py:56` sets `streaming=False` explicitly with a comment tying it to the agent role's IAM (no `InvokeModelWithResponseStream` grant).
- **New drift found**: `design.md` §3's Repository Layout still lists `scripts/upload_content.sh` and `scripts/sync_kb.py` (the original two-script split), but §11 and the actual code correctly use the merged `scripts/sync_content.py`. §3 was never updated after the merge decision. **WARNING** (docs-only, no code impact).
- **New drift found**: `state.yaml` has no batch entry for PR9b (runbooks/README/CI-synth-fix), even though the work is committed and opened as PR #16. The `apply` phase's `depends_on`/batch list stops at PR9a. **WARNING** (traceability, no code impact).
- **Least-Privilege Agent Role spec wording** — see SUGGESTION below; not a code drift, a spec-precision issue.

No other spec/design/code contradictions found.

---

## Gate Evidence (run on `feat/ops-docs` HEAD)

```
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
119 files already formatted

$ uv run mypy src infra
Success: no issues found in 43 source files

$ uv run pytest --cov=src --cov-fail-under=85 -q
408 passed, 1 warning in 13.98s
Required test coverage of 85% reached. Total coverage: 99.87%
(per-file: 100% everywhere except src/agent/main.py at 98% — the single
uncovered line is the `if __name__ == "__main__": app.run()` guard, an
intentionally-untested script entrypoint)

$ AWS_CONFIG_FILE=/dev/null AWS_SHARED_CREDENTIALS_FILE=/dev/null \
  AWS_ACCESS_KEY_ID=x AWS_SECRET_ACCESS_KEY=x AWS_SESSION_TOKEN= AWS_PROFILE= \
  CDK_DEFAULT_ACCOUNT=000000000000 CDK_DEFAULT_REGION=us-east-1 \
  AGENT_ZIP_PATH=build/agent.zip LAMBDA_ZIP_PATH=build/lambda.zip \
  npx --yes aws-cdk@2 synth --all --no-lookups --quiet
Successfully synthesized to .../cdk.out
  -> portfolio-agent-data.template.json, portfolio-agent-agent.template.json,
     portfolio-agent-api.template.json all produced; cdk.out/ deleted after inspection

$ git --no-pager log --all --oneline | wc -l
50

$ git --no-pager log --all --diff-filter=A --name-only --format= | sort -u \
  | grep -iE '\.pdf$|(^|/)content/|\.env'
(no output — zero matches across all 50 commits)
```

Test layer distribution: 406 unit tests (`tests/unit/`), 2 contract tests
(`tests/contract/test_payload_contract.py`), 0 collected in `tests/smoke/` (by design —
`test_smoke.py` is an operational script with no `def test_*`, exercised only indirectly via
`tests/unit/scripts/test_smoke_script.py`'s 19 tests of its pure logic).

Targeted Assertion Quality scan (grep-based, not a full manual read of all 43 test files): no
tautologies (`assert True`/`assert 1==1`), no pass-only test bodies, no test file where
`Mock()`/`MagicMock()`/`.call_count` occurrences exceed 2× its `assert` count. **No CRITICAL or
WARNING assertion-quality findings** from this pass.

Not independently re-run in this verification (network/binary-dependent, outside this session's
constraints): `gitleaks`, `pip-audit`. Last observed real run (PR #12's CI log, 2026-09-16): gitleaks
"85 commits scanned … no leaks found"; pip-audit "No known vulnerabilities found."

---

## Delivery-State Findings (GitHub, read-only `gh` queries)

- 16 PRs total. **7 merged** (#1–#8, covering PR0 through PR4a). **9 open**: #9 (PR5), #10 (PR5b), #11 (PR6), #12 (PR7), #13 (PR8a), #14 (PR8b), #15 (PR9a), #16 (PR9b).
- **0 reviews and 0 comments on all 16 PRs**, merged or open.
- CI status on open PRs: #9, #10, #11, #16 → SUCCESS. **#12, #13, #14, #15 → FAILURE** — root cause confirmed via `gh run view --log-failed`: those runs used the pre-fix `ci.yml` (`uv run cdk synth --all`, which fails with `error: Failed to spawn: cdk` — the exact `npx cdk` vs. `npx aws-cdk@2` name collision this project's own `RTK.md`/gotchas already documented). The fix (`npx --yes aws-cdk@2 synth`) only landed in commit `0968263`, which is on top of the whole chain (PR9b/#16) — it was never backported into PR7/PR8a/PR8b/PR9a's own branches, so their GitHub check status is stale, not currently accurate.
- The recorded delivery decision (`state.yaml`) is **`chain_strategy: stacked-to-main`** ("Each PR merges to main in order"), decided 2026-09-08. The actual chain from PR6 (#11) onward is **not** stacked-to-main: PR6 is based on PR5b's branch (not `main`), PR7 on PR6's branch, PR8a on PR7's, PR8b on PR8a's, PR9a on PR8b's, PR9b on PR9a's — this is a **feature-branch chain**, the *other* documented strategy. This is a real deviation from the recorded decision, not merely a naming issue: it means none of PR6–PR9b can be merged independently or in a different order, and a rebase conflict anywhere in the chain blocks everything above it.

---

## Findings by Severity

**CRITICAL**: None.

**WARNING**:
1. 9 of 16 opened PRs (everything from PR5 onward, including all CDK infrastructure) are not merged to `main`; "the whole stack recreatable from scratch" is only true of the tip branch today.
2. PR7 (#12), PR8a (#13), PR8b (#14), PR9a (#15) show a failing GitHub CI check caused by a stale pre-fix `ci.yml`; each branch needs the `0968263` synth fix (or a rebase onto a branch that has it) before it can show green CI.
3. Zero GitHub-native PR reviews exist across all 16 PRs. The `X.G` "fresh-context security-review + code review" gates are evidenced only by narrative text in `apply-progress`, which is not independently auditable; for 5 of the 7 already-merged PRs (PR1, PR2b, PR3a, PR3b, PR4a) that narrative doesn't even include an explicit security-review verdict (only PR0 and PR2a do).
4. The actual PR chain topology (each branch based on the previous unmerged branch) is a feature-branch chain, not the `stacked-to-main` strategy recorded as the owner's decision in `state.yaml`.
5. `state.yaml`'s `apply.batches` list has no entry for PR9b (runbooks/README/CI fix), even though it is implemented, committed, and opened as PR #16 — the apply-progress artifact undercounts real progress by one full PR.
6. `tasks.md` items 9.4 and 9.5 are unchecked despite being fully implemented (runbooks, README) — checkbox/state drift, not missing work.
7. `design.md` §3's Repository Layout still names the pre-merge `upload_content.sh` + `sync_kb.py` split instead of the actual `sync_content.py`; §11 and the code are already correct, only §3 is stale.

**SUGGESTION**:
1. `infrastructure` spec's "Least-Privilege Agent Role" requirement text says the agent runtime's role grants `InvokeModel` on both Nova Micro and Titan; the implementation is actually stricter (agent role: Nova Micro only; a separate KB service role holds Titan). Tightening the requirement wording to reflect the two-role split would remove the only spec/implementation mismatch left in the whole change.
2. The Assertion Quality Audit in this pass was a targeted grep scan (tautologies, pass-only bodies, mock/assert ratio), not a line-by-line read of all 43 test files; a deeper manual pass (especially for ghost-loop-over-possibly-empty-collection patterns) would raise confidence further before archive.
3. `gitleaks`/`pip-audit` were not independently re-run in this verification pass (network/binary constraints); the evidence cited is the last real CI run's output, not a fresh one against the current tip.
4. Consider recording the `X.G` review-gate outcomes as actual GitHub PR reviews (even after the fact, as a review comment) for PR1–PR9a before archive, so the "security review before every merged PR" success criterion has an artifact independent of `apply-progress`'s own narrative.

---

## Pre-Deploy Checklist (consolidated, supersedes prior partial lists)

**Carried risks from `apply-progress` / `design.md` §13 — status:**

| Risk | Status |
|---|---|
| Cold-session latency (~6–12s) may exceed the 10s cold p95 target | **Still open** — cannot be resolved before a real deploy; smoke test is ready to measure it honestly |
| Nova Micro may require the `us.amazon.nova-micro-v1:0` inference profile | **Still open** — agent role already grants both ARNs defensively; must run `aws bedrock list-inference-profiles --region us-east-1` before first deploy |
| Lambda's bundled boto3 may lack the `bedrock-agentcore` client | **Closed** — pinned `boto3==1.43.90`, verified locally to carry the client (PR5 apply-progress) |
| Review Workload High risk / chain strategy undecided | **Partially closed** — chain strategy *was* decided (stacked-to-main) but the actual chain built is feature-branch-chain (WARNING 4 above); this should be reconciled, not just left as "decided" |

**New pre-deploy checklist, in order:**

1. Rebase/update PR7, PR8a, PR8b, PR9a onto branches carrying the `0968263` CI synth fix (or cherry-pick it), then confirm green CI on each.
2. Obtain and record actual code + security reviews (GitHub-native or otherwise auditable) for PR1, PR2b, PR3a, PR3b, PR4a retroactively, and for PR5 through PR9b before merge.
3. Merge PR5 → PR9b to `main` in dependency order (or explicitly re-decide the chain strategy given what was actually built).
4. Confirm the Nova Micro inference-profile id (`aws bedrock list-inference-profiles --region us-east-1`).
5. Deploy `DataStack` → `AgentStack` → `ApiStack` in order.
6. Complete the manual ACM DNS-validation CNAME, then the `api` CNAME, both at DigitalOcean.
7. Run `scripts/sync_content.py` for the first content upload + ingestion.
8. Run `tests/smoke/test_smoke.py` (default N=20 sessions) and record the real warm/cold p95 split against the 3.5s/10s targets.
9. Create the manual USD 10/month AWS Budget with an 80% alert.
10. Update tasks.md checkboxes for 9.4/9.5 (already done) and add the missing PR9b entry to `state.yaml`.
11. Land the external Portfolio widget change (task 10.1) once ApiStack is deployed.
12. Only then run task 9.G's final gate and proceed to `sdd-archive`.

---

## Result

- **status**: partial
- **executive_summary**: 0 CRITICAL, 7 WARNING, 4 SUGGESTION — all code/spec/test evidence is clean (408 tests, 99.87% coverage, ruff/mypy/synth all pass locally); the gap is entirely in delivery state (9 of 16 PRs unmerged, 4 with stale failing CI, zero GitHub-native reviews anywhere, and two bookkeeping drifts in tasks.md/state.yaml).
- **artifacts**: `openspec/changes/agentcore-migration-v1/verify-report.md`; engram `sdd/agentcore-migration-v1/verify-report`
- **next_recommended**: sdd-apply (continue merging PR5–PR9b, fix stale CI on PR7/PR8a/PR8b/PR9a, reconcile chain strategy) — not sdd-archive
- **risks**: see Findings by Severity above; none are CRITICAL/code-blocking, all are delivery/traceability gaps
- **skill_resolution**: paths-injected — `sdd-verify` (+ `_shared/sdd-phase-common.md`, `strict-tdd-verify.md`), `python-testing-patterns`, `aws-well-architected-framework-review` (used only as a lens for the infrastructure findings, no full 57-question review run)
