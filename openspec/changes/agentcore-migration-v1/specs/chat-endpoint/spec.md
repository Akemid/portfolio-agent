# Chat Endpoint Specification

## Purpose

Defines the public, no-login HTTP contract for asking a CV/portfolio question and receiving a single, non-streaming answer, including validation, rate-limit surfacing, upstream error mapping, CORS, and log redaction.

## Requirements

### Requirement: Endpoint Shape

The system MUST expose exactly one public route, `POST /v1/chat`, for asking a question. Any other method or path on this route MUST return a standard 404/405 without invoking the agent.

#### Scenario: Valid method and path

- GIVEN the API is deployed at `api.sergiomondragon.com`
- WHEN a client sends `POST /v1/chat`
- THEN the request is routed to the chat handler

#### Scenario: Wrong method

- GIVEN the route `/v1/chat` exists
- WHEN a client sends `GET /v1/chat`
- THEN the system returns `405 Method Not Allowed` without invoking the agent

### Requirement: Request Contract

The system MUST accept a JSON body `{ "message": string }` and MUST reject malformed JSON, a missing `message` field, an empty string, or a message longer than 500 characters with `400 Bad Request`.

#### Scenario: Valid request body

- GIVEN a client sends `{ "message": "What is your experience with React?" }`
- WHEN the request passes validation
- THEN the handler proceeds to rate-limit checks and agent invocation

#### Scenario: Invalid JSON

- GIVEN a client sends a body that is not valid JSON
- WHEN the handler parses the body
- THEN the system returns `400 Bad Request` and does not invoke the agent

#### Scenario: Empty message

- GIVEN a client sends `{ "message": "" }`
- WHEN the handler validates the body
- THEN the system returns `400 Bad Request`

#### Scenario: Message exceeds length cap

- GIVEN a client sends a `message` of 501 characters
- WHEN the handler validates the body
- THEN the system returns `400 Bad Request` and does not invoke the agent

### Requirement: Response Contract

The system MUST respond with a single, non-streaming JSON object `{ "answer": string, "language": "en" | "es" }` on success. The system MUST NOT return the legacy paragraph-list shape.

#### Scenario: Successful answer

- GIVEN a validated, non-rate-limited request
- WHEN the agent returns an answer
- THEN the system responds `200 OK` with `{ "answer": "...", "language": "en" }`
- AND the response is delivered as a single payload, not a stream

### Requirement: Rate-Limit Surfacing

The system MUST return `429 Too Many Requests` with a `Retry-After` header when either the session or the IP limit (see `rate-limiting` spec) has been exceeded, and MUST NOT invoke the agent in that case.

#### Scenario: Session limit exceeded

- GIVEN a session has reached its daily question cap
- WHEN it sends another `POST /v1/chat`
- THEN the system returns `429` with a `Retry-After` header
- AND no agent invocation is recorded

### Requirement: Upstream Failure Mapping

The system MUST map agent-invocation errors to `502 Bad Gateway` and agent-invocation timeouts to `504 Gateway Timeout`. Error responses MUST NOT leak internal exception messages, stack traces, or AWS resource identifiers.

#### Scenario: Agent invocation fails

- GIVEN a validated, non-rate-limited request
- WHEN `InvokeAgentRuntime` raises an error
- THEN the system returns `502 Bad Gateway` with a generic error message

#### Scenario: Agent invocation times out

- GIVEN a validated, non-rate-limited request
- WHEN the agent does not respond within the configured timeout budget
- THEN the system returns `504 Gateway Timeout` with a generic error message

### Requirement: CORS Restriction

The system MUST restrict `Access-Control-Allow-Origin` to `https://sergiomondragon.com` and one additional, environment-configurable localhost origin for local development, and MUST support credentialed requests (`Access-Control-Allow-Credentials: true`) so the session cookie is sent.

#### Scenario: Allowed origin

- GIVEN a request from `https://sergiomondragon.com` with `credentials: 'include'`
- WHEN the browser performs the CORS preflight
- THEN the system allows the origin and credentials

#### Scenario: Disallowed origin

- GIVEN a request from an origin other than the configured allowlist
- WHEN the browser performs the CORS preflight
- THEN the system does not include that origin in `Access-Control-Allow-Origin`

### Requirement: Log Redaction

The system MUST NOT log full message bodies (only a truncated prefix of at most 100 characters) and MUST NOT log the session identifier in plain form; the session identifier MUST be hashed before it appears in any log line.

#### Scenario: Long message logged

- GIVEN an incoming message of 300 characters
- WHEN the handler logs the request
- THEN the log line contains at most a 100-character prefix of the message, not the full body

#### Scenario: Session id in logs

- GIVEN a request carrying a session cookie
- WHEN the handler logs the request
- THEN the log line contains a hash of the session id, never the raw id

### Requirement: End-to-End Latency Budget

The system SHOULD complete a non-streaming answer, end to end, within a p95 of 3.5 seconds, measured by the post-deploy smoke test.

#### Scenario: Smoke test latency check

- GIVEN the deployed stack
- WHEN the smoke test sends a representative question
- THEN the observed p95 latency across smoke-test runs is under 3.5 seconds
