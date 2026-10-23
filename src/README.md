# Source code

- `server.ts` starts the local Fastify server with database pool and registered plugins.
- `app.ts` defines the HTTP application, sanitized error handling, `/health` liveness probe, and `/v1/auth/verify` tenant authentication endpoint.
- `auth/` implements Phase 2A tenant API key authentication:
  - `api-key.ts`: strict key parsing (`relay_live_<public-key-id>.<secret>`), SHA-256 secret hashing, and constant-time verification (`crypto.timingSafeEqual`).
  - `tenant-store.ts`: `TenantKeyRepository` interface (Repository Pattern), `PostgresTenantKeyRepository` (parameterized SQL), and `InMemoryTenantKeyRepository`.
  - `middleware.ts`: Fastify `authenticateTenant` hook attaching `request.tenantId` and sending generic 401 responses.
- `validation/` implements Phase 2B request and header validation:
  - `notification-payload.ts`: validates channel (`email` or `push`), recipient ID (1–256 UTF-8 bytes), rendered content fields (`subject`/`body` or `title`/`body`), rejects unknown fields, preserves letter case and whitespace.
  - `idempotency-key.ts`: validates strict ASCII `Idempotency-Key` header (`0x21`–`0x7E`, 1–128 bytes, no trimming).
- `idempotency/` implements Phase 2B canonicalization and fingerprinting:
  - `canonical-serializer.ts`: Unicode NFC normalization of content fields only; versioned tagged binary serializer (`relay-notification-v1\0` with tags 0x01, 0x02, 0x10/0x11 or 0x20/0x21).
  - `fingerprint.ts`: HMAC-SHA-256 request fingerprint computation with format/key version tracking and constant-time verification.
- `config.ts` validates server settings, a loopback-only database URL with no query or fragment options, tenant-key source, a canonical Base64 fingerprint secret of at least 32 decoded bytes, and deterministic fake-provider modes.
- `database/pool.ts` creates a lazy `pg` connection pool. Creating the pool does not connect to PostgreSQL.

Phase 2B implements payload validation, Unicode NFC normalization, and canonical HMAC-SHA-256 fingerprinting. Notification acceptance transactions, database idempotency reservations, preferences, fake providers, and outbox delivery workers are not yet implemented.
