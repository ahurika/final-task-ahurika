# Tests
 
- `config.test.ts`: environment and safety validation tests.
- `health.test.ts`: minimal unauthenticated health response tests.
- `migration-safety.test.ts` & `migration-schema.test.ts`: migration script guard and static schema assertions.
- `auth.test.ts`: Phase 2A tenant authentication tests covering key parsing, SHA-256 constant-time verification, active/revocation lifecycle, parameterized database queries, tenant context attachment, uniform generic 401 responses, and credential non-leakage.
- `payload-validation.test.ts`: Phase 2B payload validation tests covering channel, recipient ID limits, email/push required content, unknown property rejection, whitespace/case preservation, strict ASCII Idempotency-Key validation, and Fastify 64 KiB bodyLimit enforcement.
- `canonical-fingerprint.test.ts`: Phase 2B canonical tagged binary serializer tests, exact hexadecimal serialization vectors, Unicode NFC normalization equivalence, case/whitespace distinction, and deterministic HMAC-SHA-256 fingerprinting.
