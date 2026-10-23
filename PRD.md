# Product Requirements Document: Relay Notification Platform

**Version:** 0.1  
**Status:** Design approved for initial prototype implementation  
**Implementation status:** Phase 1 foundation implemented; awaiting separate safety/schema review  
**Source:** Graduation Task 3 brief; assumptions are identified below.

## 1. Product summary

Relay is a multi-tenant notification platform through which client applications request notifications for their users. The platform accepts requests, checks recipient preferences, queues eligible work, delivers through channel adapters, retries eligible failures, and records delivery state while reducing duplicate sends.

The brief describes a scale of 50 million notifications per day, bursts when a client broadcasts to many recipients, preference enforcement, retries, and duplicate prevention.

## 2. Goals

- Accept and validate notification requests from client applications.
- Respect recipient notification preferences before dispatch.
- Process delivery asynchronously so provider latency does not block request acceptance.
- Support email and push in the prototype through fake provider adapters.
- Retry transient failures using bounded exponential backoff.
- Persist notification and attempt state for inspection and recovery.
- Prevent repeated processing of the same accepted request using idempotency controls.
- Demonstrate behavior under load and during a deliberate failure drill.

## 3. Non-goals

- A production-ready commercial service.
- Real email, push, SMS, or WhatsApp provider integrations.
- A guarantee that a recipient's device displays a notification exactly once.
- A complete customer analytics dashboard or billing system.
- Implementing all production-scale multi-region infrastructure in the prototype.
- Sending to real people or using real personal data in tests.

## 4. Users and actors

- **Client application:** authenticates and submits notification requests.
- **Recipient:** a user whose channel preferences affect eligibility.
- **Relay API:** validates and persists requests.
- **Queue/dispatcher:** schedules work for asynchronous processing.
- **Delivery worker:** executes a channel delivery attempt and records its outcome.
- **Fake provider:** simulates success, transient failure, permanent failure, delay, and timeout.
- **Operator/developer:** inspects health, queue depth, delivery state, and test evidence.

## 5. Functional requirements

| ID | Requirement | Acceptance evidence |
|---|---|---|
| FR-01 | Relay shall accept an authenticated client request to create a notification. | API test verifies valid request accepted and invalid/unauthenticated request rejected. |
| FR-02 | Relay shall validate required recipient, channel, rendered notification content, and idempotency information. Idempotency keys accept only ASCII bytes `0x21–0x7E`, length 1–128 bytes, with no trimming or normalization. | Validation tests cover missing/malformed fields and every idempotency-key boundary. |
| FR-03 | Relay shall support email and push channels in the prototype through separate fake provider adapters. | Integration tests show both channels can succeed and fail independently. |
| FR-04 | Relay shall enforce explicit opt-in recipient preferences before enqueuing or dispatching channel work. A missing preference or a disabled channel preference suppresses delivery. | Missing and disabled preferences both produce a suppressed result and no provider call. |
| FR-05 | Relay shall persist accepted notification state before reporting successful acceptance. | Test confirms a durable record exists after successful API response. |
| FR-06 | Relay shall process delivery asynchronously. | API response does not wait for fake provider delivery completion. |
| FR-07 | Relay shall retry eligible transient failures with bounded exponential backoff. | Tests verify attempt count, delay policy, and terminal state after the limit. |
| FR-08 | Relay shall not retry known permanent failures indefinitely. | Permanent failure becomes terminal or requires an explicitly documented manual action. |
| FR-09 | Relay shall use an idempotency mechanism to prevent duplicate acceptance/processing for repeated requests with the same key and tenant. | Repeated request returns the original result or stable identifier without creating another logical notification. |
| FR-10 | Relay shall record notification and delivery-attempt states. | API/test inspection shows queued, processing, sent, retrying, suppressed, and failed states as applicable. |
| FR-11 | Relay shall expose enough health/metrics information to inspect failures, queue backlog, attempts, and processing latency. | Evidence includes relevant logs/metrics or a documented local inspection method. |
| FR-12 | Relay shall isolate client tenants so one tenant cannot access another tenant's notification data. | Authorization tests attempt cross-tenant access and expect rejection. |

## 6. Non-functional requirements

These are design targets, not claims that the prototype already meets them.

- **NFR-01 — Capacity:** design calculations use 50 million logical notifications/day and an initial 10× peak multiplier. The load test will report achieved throughput and bottlenecks.
- **NFR-02 — Reliability:** accepted work should be durably recorded before Relay acknowledges acceptance.
- **NFR-03 — Duplicate resistance:** idempotency and durable state should prevent repeated API requests from creating duplicate logical notifications. External provider ambiguity must be documented.
- **NFR-04 — Scalability:** API and worker capacity should be scalable independently; bursts should be buffered.
- **NFR-05 — Security:** authenticate client requests, validate inputs, isolate tenants, and avoid logging secrets or unnecessary personal data.
- **NFR-06 — Observability:** provide structured logs and measures for request outcomes, queue depth/age, delivery attempts, retries, and provider failures.
- **NFR-07 — Cost awareness:** explain major infrastructure cost drivers and estimate a stated 100,000-user scenario using current public pricing sources in `DESIGN.md`.
- **NFR-08 — Testability:** provider behavior and failure modes should be controllable for repeatable automated tests.

## 7. Working assumptions requiring validation

- A notification request represents one logical notification. Channel-specific delivery attempts may be separate records.
- Initial peak planning uses 10× average traffic because the brief gives this as an example.
- Email and push are the first two prototype channels.
- Fake providers simulate delivery outcomes; no real provider credentials are required.
- PostgreSQL is the primary durable data store.
- The prototype uses a PostgreSQL transactional outbox for durable dispatch. A separate broker remains a future enhancement, not the prototype architecture.
- The prototype accepts rendered notification content only. Templates, template variables, HTML email, and attachments are out of scope for this prototype.
- The approved initial stack is Node.js LTS, TypeScript, npm, Fastify, `pg`, `node-pg-migrate`, and Vitest. This is a local prototype stack; PostgreSQL remains the authoritative data store.
- Initial estimates use 1 KB per stored logical-notification record and 2 KB per outbound payload only as rough assumptions; validate or revise them in `DESIGN.md`.
- No exact retention period, SLO, region, or cost ceiling is supplied by the brief; state and justify any values proposed in the design.

## 8. Key risks and constraints

- External provider timeout after acceptance creates ambiguity: Relay may not know whether the provider delivered the message.
- Random-only failures make tests flaky; core automated tests use deterministic fake-provider modes. Optional random simulation must not control core test outcomes.
- Retry storms can amplify an outage; retries must be bounded, delayed, and subject to provider-health controls.
- A single hot tenant or broadcast can dominate queue and database resources.
- The prototype cannot reproduce production scale exactly on a typical laptop or free service tier.

## 9. Acceptance checklist

- [ ] Design document contains all 12 required sections.
- [ ] Architecture diagram and API contract are documented before implementation.
- [ ] At least five design patterns are explained in the design.
- [ ] Prototype uses at least three selected patterns, with code comments identifying them.
- [ ] Email and push fake-provider paths are tested.
- [ ] Preference suppression, retries, and duplicate-request behavior are tested.
- [ ] Load-test method, actual results, and first bottleneck are recorded.
- [ ] One deliberate failure drill and actual observed behavior are documented.
- [ ] Walkthrough video and mock-interview notes are linked in submission evidence.
- [ ] No unverified claims or secrets are committed.
