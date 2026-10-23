# Agent Instructions — Relay Task 3

## Mandatory first steps
Before suggesting or changing implementation:
1. Read `AGENTS.md`, `PRD.md`, `DESIGN.md`, and `LEARNING-LOG.md`.
2. State the current phase: requirements, design, implementation, testing, or evidence.
3. Identify the relevant PRD requirement IDs and DESIGN.md section(s).
4. List unresolved assumptions that affect the proposed work.
5. Propose a plan and wait for approval before making broad or destructive changes.

## Current phase gate
The project began in **requirements/design phase**. The user has confirmed that all 12 sections of `DESIGN.md` have been reviewed and the design is ready for the initial prototype. Application implementation may begin only in response to a separate, explicit implementation instruction; documentation-reconciliation tasks do not authorize application-code changes.

## Approved initial stack
- Node.js LTS, TypeScript, npm, Fastify, `pg`, `node-pg-migrate`, and Vitest.
- PostgreSQL is authoritative; use its transactional outbox. Do not add an ORM or separate broker to the initial local prototype.
- Use fake email/push providers and synthetic data only. Do not connect to Neon or real providers in the initial local prototype.

## Scope
The prototype critical path is: authenticated request (or clearly documented local test auth), validation, idempotency, durable acceptance, preference check, asynchronous dispatch, fake email and push providers, bounded retries, delivery state, and observable test evidence.

## Reliability constraints
- Never claim exactly-once delivery end-to-end across a provider and recipient device.
- Distinguish duplicate API requests, duplicate worker processing, provider acceptance, and recipient-visible delivery.
- Persist accepted work before acknowledging acceptance.
- If a database and broker are separate, explicitly handle the dual-write problem; evaluate a transactional outbox.
- Retries must be bounded, use backoff, and distinguish transient from permanent failures.
- Make fake provider failures testable and reproducible; randomness alone must not make automated tests flaky.
- Ensure idempotency is enforced by a durable constraint/transaction, not only an in-memory check.

## Security and privacy
- Never add real secrets, tokens, credentials, or personal recipient data to source control.
- Use `.env.example` with placeholders only.
- Validate all input and enforce tenant ownership on reads and writes.
- Avoid logging full message content, credentials, or unnecessary personal data.
- Do not call real notification providers unless the user explicitly changes scope and approves the consequences.

## Engineering expectations
- Keep modules small and responsibilities explicit.
- Use types and validation at API boundaries.
- Add tests for success, validation errors, tenant isolation, preference suppression, idempotency, retry behavior, and permanent failure.
- Explain every dependency and why it is needed. Prefer free/local tools where practical, but do not sacrifice the brief's evidence requirements.
- Add comments identifying at least three design patterns in the implementation, but only where the patterns genuinely exist.
- Never claim a test passed unless it was actually run and its output was inspected.
- Record important decisions, rejected alternatives, corrections, and lessons in `LEARNING-LOG.md`.

## Change control
- Do not delete or overwrite existing project files without showing the proposed change and obtaining approval.
- Do not change the approved design silently. If implementation reveals a mismatch, stop, explain the evidence, propose a design update, and record it.
- Do not invent load-test results, failure-drill results, interview notes, video links, cost figures, or citations.
- Keep generated test output and evidence in the designated folders.

## Required response format for implementation work
1. Phase and relevant FR/NFR IDs.
2. Proposed change and files affected.
3. Design decision being implemented.
4. Tests to run and expected evidence.
5. Actual result, clearly distinguished from expectation.
