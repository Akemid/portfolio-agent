# Session Identity Specification

## Purpose

Defines the server-issued, HttpOnly session cookie that attributes anonymous requests to a rate-limit counter without login, per the decision recorded in `docs/blog/session-identity-httponly-cookie.md`.

## Requirements

### Requirement: Cookie Issuance

The system MUST issue a session cookie named `session_id` when a request arrives with no cookie or with a cookie referencing an unknown or expired session. The system MUST NOT issue a new cookie when a valid, unexpired session already exists.

#### Scenario: First-time visitor

- GIVEN a request to `POST /v1/chat` with no `session_id` cookie
- WHEN the handler processes the request
- THEN the system creates a new session record
- AND the response includes `Set-Cookie: session_id=<opaque id>; HttpOnly; Secure; SameSite=Lax; Path=/`

#### Scenario: Returning visitor with a valid session

- GIVEN a request carrying a `session_id` cookie that maps to an unexpired session
- WHEN the handler processes the request
- THEN the system reuses the existing session
- AND no `Set-Cookie` header is sent

### Requirement: Cookie Attributes

Every session cookie the system sets MUST include `HttpOnly`, `Secure`, `SameSite=Lax`, and `Path=/`. The system MUST NOT set any cookie attribute that allows page JavaScript to read the value.

#### Scenario: Cookie attribute check

- GIVEN a new session is created
- WHEN the `Set-Cookie` header is generated
- THEN it contains `HttpOnly; Secure; SameSite=Lax; Path=/`

### Requirement: Session Identifier Quality

The session identifier MUST be an opaque, cryptographically random value with at least 128 bits of entropy. The identifier MUST NOT encode or derive from any personally identifiable information.

#### Scenario: Identifier generation

- GIVEN a new session is created
- WHEN the identifier is generated
- THEN it is drawn from a cryptographically secure random source
- AND it contains no user-supplied or personal data

### Requirement: Fixed Session TTL

Each session MUST expire exactly 24 hours after issuance (fixed TTL, not extended by activity). The system MUST rely on the DynamoDB TTL attribute to expire the record; expiry MUST require no manual cleanup.

#### Scenario: Session within TTL

- GIVEN a session was issued 10 hours ago
- WHEN a request arrives with that session's cookie
- THEN the system treats the session as valid and does not extend its expiry

#### Scenario: Session past TTL

- GIVEN a session was issued 25 hours ago
- WHEN a request arrives with that session's cookie
- THEN the system treats the cookie as unknown and issues a new session

### Requirement: Tamper and Unknown-ID Handling

When a request presents a `session_id` cookie that does not match any stored session (unknown, expired, or tampered value), the system MUST silently issue a fresh session rather than returning an error.

#### Scenario: Tampered cookie value

- GIVEN a request carries a `session_id` cookie value that does not exist in the session store
- WHEN the handler looks up the session
- THEN the system creates a new session and returns a new `Set-Cookie` header
- AND the request is not rejected because of the invalid cookie

### Requirement: No PII in Session Records

Session records stored in DynamoDB MUST NOT contain personally identifiable information (name, email, IP address stored long-term, or message content). Records MUST contain only the session id, counters, and timestamps needed for rate limiting.

#### Scenario: Session record contents

- GIVEN a session is created and used for several questions
- WHEN the session record is inspected
- THEN it contains only the session id, question count, and expiry timestamp
