# Deploy runbook

First-time and repeat deploys of the three CDK stacks (`portfolio-agent-data`,
`portfolio-agent-agent`, `portfolio-agent-api`), plus the one-time DNS/ACM
steps a fresh account needs before `api.sergiomondragon.com` resolves.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12 dependency manager)
- Node.js (for the AWS CDK CLI, run via `npx aws-cdk@2` — this repo has no
  `package.json`, the CLI is fetched on demand)
- AWS credentials for the target account, with permission to create the
  resources in `infra/stacks/*.py`
- `cdk bootstrap`, run once per account/region

```bash
uv sync --all-groups
npx --yes aws-cdk@2 bootstrap aws://<ACCOUNT_ID>/us-east-1
```

## Quick path

1. Build both deployment zips.
2. Deploy the three stacks in order.
3. Complete the one-time DNS/ACM steps.
4. Run the first content sync.
5. Run the smoke test.

### 1. Build the deployment zips

```bash
scripts/build_agent.sh
scripts/build_lambda.sh
```

This produces `build/agent.zip` (the AgentCore Runtime package, arm64 wheels
of the `agent` dependency group plus `src/agent`) and `build/lambda.zip` (the
Lambda package, arm64 wheels of `boto3` plus `src/api`). Both scripts fail
loudly if the zip exceeds the 250 MB AWS limit.

### 2. Deploy the stacks, in order

`portfolio-agent-data` has no dependencies; `portfolio-agent-agent` depends on
it; `portfolio-agent-api` depends on both. `infra/app.py` already encodes
this order and the explicit stack dependencies, so `--all` deploys them
correctly — but confirm the exact stack names if you ever target one alone:

```bash
npx --yes aws-cdk@2 deploy --all --require-approval broadening
```

To deploy one stack at a time instead (useful when only one changed):

```bash
npx --yes aws-cdk@2 deploy portfolio-agent-data
npx --yes aws-cdk@2 deploy portfolio-agent-agent
npx --yes aws-cdk@2 deploy portfolio-agent-api
```

TO VERIFY AT DEPLOY: `portfolio-agent-api` is expected to stall until the ACM
certificate below reaches `ISSUED`, because the custom domain depends on it.
CloudFormation's exact behaviour here (a long wait versus a timeout) has not
been observed on a real account. If it times out, create the validation CNAME
from step 4 and re-run the deploy.

### 3. Confirm the Nova Micro inference profile id

TO VERIFY AT DEPLOY: `infra/stacks/agent_stack.py` grants `bedrock:InvokeModel`
on both the plain foundation-model ARN and the
`us.amazon.nova-micro-v1:0` cross-region inference-profile ARN, but the exact
profile id has not been confirmed against a real account. Before the first
deploy:

```bash
aws bedrock list-inference-profiles --region us-east-1 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId, 'nova-micro')]"
```

If the id differs from `us.amazon.nova-micro-v1:0`, update
`NOVA_MICRO_INFERENCE_PROFILE_ID` in `infra/stacks/agent_stack.py` and
`MODEL_ID` will follow automatically (`agent_stack.py` sets it from the same
constant).

### 4. ACM DNS validation (one-time per account)

DNS for `sergiomondragon.com` lives at DigitalOcean, not Route 53, so the ACM
certificate cannot validate itself — you add the validation record by hand.

```bash
aws acm describe-certificate \
  --certificate-arn <CertificateValidationHint output from portfolio-agent-api> \
  --region us-east-1 \
  --query "Certificate.DomainValidationOptions"
```

1. Copy the returned `Name` and `Value`.
2. Create that as a CNAME record at DigitalOcean's DNS panel for
   `sergiomondragon.com`.
3. Wait for the certificate to reach `ISSUED` — ACM polls DNS automatically
   once the record exists; `cdk deploy` for `portfolio-agent-api` will not
   finish creating until then.

### 5. Point `api.sergiomondragon.com` at the API

Create a second CNAME at DigitalOcean:

- Name: `api`
- Value: the `ApiRegionalDomainName` output from `portfolio-agent-api`

### 6. First content sync

```bash
uv run python scripts/sync_content.py --content-dir <your-content-dir>
```

`<your-content-dir>` must mirror the S3 layout under `content/`, e.g.
`<your-content-dir>/cv/resume.pdf`, `<your-content-dir>/portfolio/en/about.md`.
Bucket, Knowledge Base id, and data source id resolve automatically from the
`portfolio-agent-data` stack outputs. Add `--dry-run` first to preview the
planned upload with no AWS calls. See
[`content-sync.md`](./content-sync.md) for the full procedure.

### 7. Smoke test

```bash
uv run python tests/smoke/test_smoke.py
```

Resolves the API URL from the `portfolio-agent-api` stack output
automatically. Reports warm and cold-start (first-request-of-session)
latency separately against the split SLO (warm p95 < 3.5 s, cold p95 < 10 s),
confirms the `{answer, language}` response shape, and confirms exactly one
`Access-Control-Allow-Origin` header on a real response — this was a carried
risk from the ApiStack CDK work (API Gateway CORS vs. the Lambda's own CORS
headers) and this is the check that closes it.

Add `--expect-substring "<a fact only in the new content>"` to confirm the
agent is actually grounded on your content, not just responding.

### 8. Set up the cost alarm

Not automated by this repo — see [`cost.md`](./cost.md) for the manual
budget-alarm steps.

## Checklist

- [ ] `build/agent.zip` and `build/lambda.zip` built and under 250 MB
- [ ] All three stacks deployed (`data` → `agent` → `api`)
- [ ] Nova Micro inference profile id confirmed against the real account
- [ ] ACM certificate `ISSUED` and the `api` CNAME resolves
- [ ] First content sync completed
- [ ] Smoke test passes, including the single-CORS-header check
- [ ] USD 10/month budget alarm created

## What to check in CloudWatch

| Log group | What it holds |
|-----------|----------------|
| `/aws/lambda/portfolio-agent-chat` | Lambda application logs — hashed session/IP ids only, never raw |
| `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` | AgentCore Runtime logs (agent invocation, tool calls) |
| `/aws/apigateway/portfolio-agent-chat-access` | API Gateway access logs — request id, source IP, status, latency (AWS-managed retention) |

TO VERIFY AT DEPLOY: the exact AgentCore Runtime log group/stream naming
convention has not been confirmed against a real deployment — check
`aws logs describe-log-groups --log-group-name-prefix /aws/bedrock-agentcore`
after the first invocation if the name above does not match.

## Next step

Something wrong after a deploy? See [`rollback.md`](./rollback.md).
