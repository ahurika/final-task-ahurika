# final-task-ahurika — Part 3: Relay Notification Platform

This branch contains the design and prototype implementation work for Part 3 (Relay Notification Platform) of the graduation assessment.

## Current phase
**Implementation — Phase 1: project foundation.** The initial local TypeScript/Fastify foundation, configuration validation, health endpoint, PostgreSQL pool factory, initial schema migration, and focused tests are in scope. Notification acceptance and delivery are not implemented in this phase.

## Confirmed working assumptions
- Daily volume: 50 million logical notifications.
- Initial peak planning assumption: 10× the average rate.
- Prototype channels: email and push notifications.
- Provider behavior: fake providers with deterministic outcomes for core tests.
- Primary database: PostgreSQL.
- Approved initial stack: Node.js LTS, TypeScript, npm, Fastify, `pg`, `node-pg-migrate`, and Vitest.
- The prototype must demonstrate preference checks, asynchronous processing, retries, and duplicate prevention.
- The first prototype uses local PostgreSQL only. Neon is a possible future host that requires separate approval.
- A running PostgreSQL service does not mean Relay has selected or verified a database. Do not run migrations until the local database is identified and approved.

These are design assumptions, not measured production results. Record changes and reasons in `LEARNING-LOG.md`.

## Folder guide
- `PRD.md`: product scope, requirements, acceptance criteria, and traceability.
- `DESIGN.md`: the required 12-section system design document.
- `AGENTS.md`: instructions for AI coding agents working in this repository.
- `.agent/rules/`: project-wide engineering constraints.
- `.agent/skills/relay-engineering/SKILL.md`: repeatable workflow for design, implementation, and verification.
- `docs/`: supporting API and architecture notes, if they become useful.
- `src/`: Phase 1 server, configuration, and PostgreSQL pool foundation.
- `tests/`: automated tests.
- `migrations/`: PostgreSQL schema migrations, not applied automatically.
- `load-tests/`: load-test scripts and configuration.
- `evidence/`: actual test output, failure-drill report, diagram exports, interview notes, and video link/notes.

## Working rules
1. Read `AGENTS.md`, `PRD.md`, and `DESIGN.md` before implementation.
2. Do not invent test results, benchmark results, costs, credentials, or evidence.
3. Keep fake providers clearly labelled as simulations.
4. Never commit real secrets. Use `.env.example` for variable names and placeholders only.
5. Do not claim end-to-end exactly-once delivery through external providers. Explain the boundary and mitigations.
6. Preserve the original task repositories and the graduation hub's existing files.

## Local foundation setup

Use Node.js `^22.12.0`, `^24.0.0`, or `>=26.0.0` and npm. Install dependencies with `npm install`, then set local-only values in `.env`. The `.gitignore` contains rules for `.env` and `.env.*`, but this workspace is not currently a Git repository, so effective Git ignore status cannot be confirmed yet. Do not enter real secrets until Git ignore behavior can be verified. Never paste database credentials or HMAC secrets into chat.

Dependency roles:
- Fastify serves the HTTP health endpoint.
- `pg` provides the PostgreSQL connection pool.
- `node-pg-migrate` applies the checked-in schema migration when explicitly invoked.
- TypeScript provides the typed source build.
- Vitest runs the focused automated tests.

The server requires a local PostgreSQL URL and the dedicated fingerprint HMAC key; its `/health` endpoint is a process health check and does not query the database. The server and migration preflight allow only `localhost`, IPv4 loopback, or IPv6 loopback PostgreSQL URLs and reject query parameters or fragments, including host overrides, before the migration CLI starts.

Run `npm run typecheck`, `npm test`, and `npm run build` to verify the foundation. The initial migration is prepared but is not applied automatically; confirm the intended local database before running any migration command.
