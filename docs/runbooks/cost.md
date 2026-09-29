# Cost runbook

Estimate, the two app-level caps that bound it, and how to set the budget
alarm this repo does not create for you.

## Estimate (design.md §7)

Assumption: ~1,000 questions/month across ~300 sessions, `us-east-1`,
on-demand everywhere.

| Component | Basis | Monthly |
|-----------|-------|---------|
| API Gateway HTTP API | ~2,000 requests | $0.01 |
| Lambda | 1,000 invocations × ~2.5 s × 512 MB | $0.02 |
| DynamoDB on-demand | ~3,000 writes + ~2,000 reads; TTL deletes are free | $0.01 |
| S3 (content + agent code) | < 100 MB | $0.01 |
| S3 Vectors | ~2 MB stored, 1,000 queries | $0.02 |
| Knowledge Base embeddings (Titan V2) | ~60k re-index tokens + 1,000 query embeds | $0.01 |
| Nova Micro | ~2.5M input + ~0.2M output tokens | $0.12 |
| **AgentCore Runtime microVM** | 300 sessions × ~910 s idle memory + 1,000 × ~1.5 s compute | **$0.40** |
| CloudWatch logs + traces | ~50 MB ingest | $0.05 |
| ACM certificate | public cert for an AWS service | $0.00 |
| Route 53 | not used — DNS stays at DigitalOcean | $0.00 |
| **Total** | | **≈ USD 0.65/month** |

Under the USD 10/month ceiling with roughly 15x headroom.

### What drives cost

1. **AgentCore Runtime idle memory is ~60% of the bill.** It bills from
   microVM boot until session termination, including idle time between
   questions in the same session — not CPU. CPU is billed only while the
   agent is actually computing; I/O wait is free. The main lever is the
   runtime's idle-session timeout, not the number of questions asked.
2. **Nova Micro tokens** scale with question/answer length and how much
   retrieved context gets included in the prompt.
3. **S3 Vectors queries** scale with the number of questions (one retrieval
   per question), not with corpus size.

### Why the estimate does not bound the worst case

The rate limits (10/session/day, 5/IP/minute) bound cost *per identity*, not
globally. If every one of 300 sessions/day saturated its daily cap, that is
an ~$18/month worst case — which is why a global budget alarm is still
needed as a backstop.

## The two app-level caps already in place

These bound cost independently of the budget alarm below, enforced in
`src/api/adapters/dynamo_rate_limiter.py` and `infra/stacks/api_stack.py`:

| Cap | Value | Where |
|-----|-------|-------|
| Per-session daily limit | 10 questions/session/day | DynamoDB conditional write, `DynamoRateLimiter` |
| Per-IP per-minute limit | 5 requests/IP/minute (weighted sliding window) | DynamoDB conditional write, `DynamoRateLimiter` |
| Lambda reserved concurrency | 5 | `infra/stacks/api_stack.py`, hard cap on concurrent AgentCore/Bedrock spend |
| API Gateway stage throttle | burst 20 / rate 10 req/s | `infra/stacks/api_stack.py`, coarse backstop independent of the DynamoDB checks |

## Create the USD 10/month budget alarm

Not created by this repo's CDK stacks — a manual, one-time step per account.

### Console

1. Open **AWS Budgets** → **Create budget**.
2. Choose **Monthly cost budget**, set the amount to **USD 10**.
3. Add an alert threshold at **80%** (USD 8), notifying your email.

### CLI

```bash
aws budgets create-budget \
  --account-id <ACCOUNT_ID> \
  --budget '{
    "BudgetName": "portfolio-agent-monthly",
    "BudgetLimit": {"Amount": "10", "Unit": "USD"},
    "TimeUnit": "MONTHLY",
    "BudgetType": "COST"
  }' \
  --notifications-with-subscribers '[{
    "Notification": {
      "NotificationType": "ACTUAL",
      "ComparisonOperator": "GREATER_THAN",
      "Threshold": 80
    },
    "Subscribers": [{"SubscriptionType": "EMAIL", "Address": "<your-email>"}]
  }]'
```

## Checklist

- [ ] Budget alarm created at USD 10/month with an 80% alert
- [ ] Alert email confirmed (AWS sends a subscription-confirmation email —
      it does nothing until confirmed)

## Next step

Need to tear the stack down instead? See [`rollback.md`](./rollback.md).
