# Rate Limiting Specification

## Purpose

Defines the two-layer throttle (per-session daily cap, per-IP per-minute cap) that MUST reject excess requests before any model invocation happens.

## Requirements

### Requirement: Per-Session Daily Cap

The system MUST allow at most 10 questions per session within a fixed daily window in UTC (the window resets at 00:00 UTC, not 24 hours after first use). The threshold MUST be configurable via an environment variable.

#### Scenario: Within daily cap

- GIVEN a session has asked 9 questions today (UTC)
- WHEN it sends a 10th question
- THEN the system allows the request and increments the counter to 10

#### Scenario: Daily cap exceeded

- GIVEN a session has asked 10 questions today (UTC)
- WHEN it sends an 11th question
- THEN the system returns `429` and does not invoke the agent

### Requirement: Per-IP Per-Minute Cap

The system MUST allow at most 5 requests per rolling 60-second window per client IP address. The client IP MUST be taken from the API Gateway-provided source IP, not from client-controlled headers such as `X-Forwarded-For`. The threshold MUST be configurable via an environment variable.

#### Scenario: Within per-minute cap

- GIVEN an IP has made 4 requests in the last 60 seconds
- WHEN it makes a 5th request
- THEN the system allows the request

#### Scenario: Per-minute cap exceeded

- GIVEN an IP has made 5 requests in the last 60 seconds
- WHEN it makes a 6th request
- THEN the system returns `429` and does not invoke the agent

#### Scenario: Header spoofing attempt

- GIVEN a request carries a forged `X-Forwarded-For` header
- WHEN the system determines the client IP
- THEN it uses the API Gateway source IP, ignoring the forged header

### Requirement: Check-and-Increment Before Invocation

The system MUST evaluate and increment both counters, and confirm neither limit is exceeded, strictly before calling `InvokeAgentRuntime`. If either limit is exceeded, the system MUST short-circuit and MUST NOT call the agent.

#### Scenario: Limit check ordering

- GIVEN a request that would exceed the per-IP limit
- WHEN the handler processes it
- THEN the rate-limit check runs and rejects the request before any agent invocation is attempted

### Requirement: Counter Storage with TTL

Rate-limit counters MUST be stored in DynamoDB with a TTL attribute matching each counter's window (end of UTC day for the session counter, 60 seconds rolling for the IP counter), so expired counters are removed automatically with no manual cleanup.

#### Scenario: Counter expiry

- GIVEN a session counter's TTL has passed
- WHEN DynamoDB's TTL sweep runs
- THEN the counter item is removed without any application code running

### Requirement: 429 Response Shape

Every `429` response MUST include a `Retry-After` header (seconds until the limit resets) and a JSON body identifying which limit was hit, without leaking internal identifiers.

#### Scenario: 429 body and header

- GIVEN a request that exceeds the per-IP limit
- WHEN the system rejects it
- THEN the response is `429` with `Retry-After: <seconds>` and a body such as `{ "error": "rate_limited", "scope": "ip" }`
