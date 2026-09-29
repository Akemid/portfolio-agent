# Rollback runbook

Fastest mitigation first, then full infrastructure rollback.

## Quick path

1. Point the portfolio widget back at the legacy endpoint (seconds, no AWS
   changes).
2. If the new stack itself needs to come down, `cdk destroy` in reverse
   order.
3. Decide what to do with the retained data.

### 1. Point the widget back at the legacy endpoint

The fastest mitigation for a bad deploy or a misbehaving agent: revert the
portfolio frontend's chat widget config to call the legacy
[ChatBotAPI](https://github.com/Akemid/ChatBotAPI) endpoint instead of
`https://api.sergiomondragon.com/v1/chat`. This is a frontend-only change in
a different repository — no AWS action needed, and it takes effect as soon
as that revert ships.

Use this first while you investigate; only proceed to infrastructure
rollback if the AWS stack itself must be torn down.

### 2. Infrastructure rollback — `cdk destroy` in reverse order

Destroy in the reverse of the deploy order, since `portfolio-agent-api`
depends on `portfolio-agent-agent`, which depends on `portfolio-agent-data`:

```bash
npx --yes aws-cdk@2 destroy portfolio-agent-api
npx --yes aws-cdk@2 destroy portfolio-agent-agent
npx --yes aws-cdk@2 destroy portfolio-agent-data
```

### 3. What `RETAIN` means

`portfolio-agent-data` sets `RemovalPolicy.RETAIN` on every stateful
resource (`infrastructure` spec, *Data Retention on Destroy*): the S3
content bucket, the S3 Vectors bucket and index, the DynamoDB sessions
table, and the Bedrock Knowledge Base. `cdk destroy portfolio-agent-data`
removes the CloudFormation stack but **leaves these resources running** —
they are not deleted, and you keep paying for their storage until you
delete them by hand:

```bash
aws s3 rm s3://<content-bucket-name> --recursive && aws s3api delete-bucket --bucket <content-bucket-name>
aws dynamodb delete-table --table-name portfolio-agent-sessions
aws bedrock-agent delete-knowledge-base --knowledge-base-id <id>
aws s3vectors delete-index --vector-bucket-name <name> --index-name portfolio-agent-index
aws s3vectors delete-vector-bucket --vector-bucket-name <name>
```

Only run these if you are certain you want the content, session history,
and vector index gone for good — a `cdk deploy portfolio-agent-data` after a
`destroy` will fail to recreate a table/bucket that already exists, so you
would need to delete the old ones first anyway if you want a truly clean
slate.

### 4. Redeploy from scratch

```bash
npx --yes aws-cdk@2 deploy portfolio-agent-data
npx --yes aws-cdk@2 deploy portfolio-agent-agent
npx --yes aws-cdk@2 deploy portfolio-agent-api
```

If the retained resources from a prior deploy still exist (you skipped step
3), `portfolio-agent-data` picks them back up under the same logical names —
no data is lost. If you deleted them, this is a genuine fresh start: run the
first content sync again (see [`content-sync.md`](./content-sync.md)) and
redo the one-time ACM/DNS steps (see [`deploy.md`](./deploy.md)).

## Checklist

- [ ] Widget pointed back at the legacy endpoint, if this is an active incident
- [ ] Stacks destroyed in order: `api` → `agent` → `data`
- [ ] Decision made and acted on for the retained S3/DynamoDB/Knowledge Base
      resources
- [ ] Redeploy verified with the smoke test, if redeploying

## Next step

Redeploying from scratch? Follow [`deploy.md`](./deploy.md) from the top.
