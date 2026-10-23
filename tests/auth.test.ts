import { describe, expect, it, vi } from "vitest";
import type { Pool } from "pg";
import {
  parseApiKey,
  hashApiKeySecret,
  verifySecretDigest,
  generateApiKey,
  isKeyActive,
  InMemoryTenantKeyRepository,
  PostgresTenantKeyRepository,
  GENERIC_UNAUTHORIZED_RESPONSE,
  type TenantApiKeyRecord,
} from "../src/auth";
import { buildApp } from "../src/app";

describe("Phase 2A — Tenant Authentication", () => {
  describe("API Key Parsing (parseApiKey)", () => {
    it("successfully parses valid API keys with base64url secrets", () => {
      const generated = generateApiKey("pk_test_123");
      const parsed = parseApiKey(generated.rawKey);

      expect(parsed).not.toBeNull();
      expect(parsed?.publicKeyId).toBe("pk_test_123");
      expect(parsed?.secret).toBe(generated.secret);
    });

    it("successfully parses valid API keys with hex and padded base64 secrets", () => {
      const hexKey =
        "relay_live_pk_hex_123.0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
      const parsedHex = parseApiKey(hexKey);
      expect(parsedHex?.publicKeyId).toBe("pk_hex_123");
      expect(parsedHex?.secret.length).toBe(64);

      const base64Key = "relay_live_pk_b64.YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0NTY=";
      const parsedB64 = parseApiKey(base64Key);
      expect(parsedB64?.publicKeyId).toBe("pk_b64");
      expect(parsedB64?.secret).toBe("YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0NTY=");
    });

    it("rejects non-string or empty inputs", () => {
      expect(parseApiKey("")).toBeNull();
      expect(parseApiKey(null)).toBeNull();
      expect(parseApiKey(undefined)).toBeNull();
      expect(parseApiKey(12345)).toBeNull();
      expect(parseApiKey({})).toBeNull();
    });

    it("rejects keys without the relay_live_ prefix", () => {
      expect(parseApiKey("relay_test_pk.secret1234567890")).toBeNull();
      expect(parseApiKey("live_pk.secret1234567890")).toBeNull();
      expect(parseApiKey("pk.secret1234567890")).toBeNull();
    });

    it("rejects keys missing a period separator", () => {
      expect(parseApiKey("relay_live_pk123secret1234567890")).toBeNull();
    });

    it("rejects keys with empty public key identifier or empty secret", () => {
      expect(parseApiKey("relay_live_.secret1234567890")).toBeNull();
      expect(parseApiKey("relay_live_pk.")).toBeNull();
    });

    it("rejects keys with multiple periods", () => {
      expect(parseApiKey("relay_live_pk.part1.part2")).toBeNull();
    });

    it("rejects keys with whitespace or non-permitted characters without trimming", () => {
      expect(parseApiKey(" relay_live_pk.secret1234567890")).toBeNull();
      expect(parseApiKey("relay_live_pk.secret1234567890 ")).toBeNull();
      expect(parseApiKey("relay_live_pk space.secret1234567890")).toBeNull();
      expect(parseApiKey("relay_live_pk.secret with space")).toBeNull();
      expect(parseApiKey("relay_live_pk\n.secret1234567890")).toBeNull();
    });

    it("rejects oversized credentials exceeding length limit", () => {
      const hugeKey = "relay_live_" + "a".repeat(600);
      expect(parseApiKey(hugeKey)).toBeNull();
    });
  });

  describe("Cryptographic Hashing and Constant-time Verification", () => {
    it("computes a 32-byte SHA-256 digest for a secret", () => {
      const digest = hashApiKeySecret("super-secret-value");
      expect(Buffer.isBuffer(digest)).toBe(true);
      expect(digest.length).toBe(32);
    });

    it("verifies matching secrets using constant-time comparison", () => {
      const secret = "test-secret-value-1234567890";
      const digest = hashApiKeySecret(secret);

      expect(verifySecretDigest(secret, digest)).toBe(true);
    });

    it("rejects non-matching secrets", () => {
      const secret = "test-secret-value-1234567890";
      const digest = hashApiKeySecret(secret);

      expect(verifySecretDigest("wrong-secret-value-1234567890", digest)).toBe(false);
    });

    it("rejects digest buffers of incorrect length", () => {
      const secret = "test-secret-value-1234567890";
      const shortDigest = Buffer.alloc(16, 0);

      expect(verifySecretDigest(secret, shortDigest)).toBe(false);
    });
  });

  describe("Lifecycle and Inactivity Checks (isKeyActive)", () => {
    const validDigest = Buffer.alloc(32, 1);

    it("marks an unrevoked, unrotated key as active", () => {
      const record: TenantApiKeyRecord = {
        publicKeyId: "pk_1",
        tenantId: "tenant_abc",
        secretDigest: validDigest,
        createdAt: new Date(),
        lastUsedAt: null,
        revokedAt: null,
        rotationValidUntil: null,
      };

      expect(isKeyActive(record)).toBe(true);
    });

    it("marks an explicitly revoked key as inactive", () => {
      const record: TenantApiKeyRecord = {
        publicKeyId: "pk_1",
        tenantId: "tenant_abc",
        secretDigest: validDigest,
        createdAt: new Date(),
        lastUsedAt: null,
        revokedAt: new Date(),
        rotationValidUntil: null,
      };

      expect(isKeyActive(record)).toBe(false);
    });

    it("allows a superseded key during active 24-hour rotation overlap", () => {
      const now = new Date();
      const futureRotation = new Date(now.getTime() + 60 * 60 * 1000); // 1 hour in future

      const record: TenantApiKeyRecord = {
        publicKeyId: "pk_1",
        tenantId: "tenant_abc",
        secretDigest: validDigest,
        createdAt: new Date(),
        lastUsedAt: null,
        revokedAt: null,
        rotationValidUntil: futureRotation,
      };

      expect(isKeyActive(record, now)).toBe(true);
    });

    it("marks a superseded key as inactive after rotation overlap expires", () => {
      const now = new Date();
      const pastRotation = new Date(now.getTime() - 1000); // 1 second in past

      const record: TenantApiKeyRecord = {
        publicKeyId: "pk_1",
        tenantId: "tenant_abc",
        secretDigest: validDigest,
        createdAt: new Date(),
        lastUsedAt: null,
        revokedAt: null,
        rotationValidUntil: pastRotation,
      };

      expect(isKeyActive(record, now)).toBe(false);
    });

    it("rejects records with invalid or NaN rotation dates", () => {
      const record: TenantApiKeyRecord = {
        publicKeyId: "pk_1",
        tenantId: "tenant_abc",
        secretDigest: validDigest,
        createdAt: new Date(),
        lastUsedAt: null,
        revokedAt: null,
        rotationValidUntil: new Date("invalid-date-string"),
      };

      expect(isKeyActive(record)).toBe(false);
    });
  });

  describe("PostgresTenantKeyRepository (Parameterized Queries)", () => {
    it("executes strictly parameterized SQL queries without credential interpolation", async () => {
      const mockQuery = vi.fn().mockResolvedValue({
        rows: [
          {
            public_key_id: "pk_sql_test",
            tenant_id: "11111111-1111-1111-1111-111111111111",
            secret_digest: Buffer.alloc(32, 7),
            created_at: new Date().toISOString(),
            last_used_at: null,
            revoked_at: null,
            rotation_valid_until: null,
          },
        ],
      });

      const mockPool = { query: mockQuery } as unknown as Pool;
      const repository = new PostgresTenantKeyRepository(mockPool);

      const record = await repository.findByPublicKeyId("pk_sql_test");

      expect(mockQuery).toHaveBeenCalledTimes(1);
      const [sql, params] = mockQuery.mock.calls[0];

      // Verifies query is parameterized with $1 and does not interpolate parameters
      expect(sql).toContain("WHERE public_key_id = $1");
      expect(params).toEqual(["pk_sql_test"]);
      expect(record?.tenantId).toBe("11111111-1111-1111-1111-111111111111");
      expect(record?.secretDigest).toEqual(Buffer.alloc(32, 7));
    });

    it("returns null when no matching record is found", async () => {
      const mockQuery = vi.fn().mockResolvedValue({ rows: [] });
      const mockPool = { query: mockQuery } as unknown as Pool;
      const repository = new PostgresTenantKeyRepository(mockPool);

      const record = await repository.findByPublicKeyId("pk_unknown");
      expect(record).toBeNull();
    });
  });

  describe("Fastify Tenant Authentication Integration", () => {
    const repository = new InMemoryTenantKeyRepository();
    const app = buildApp({ tenantKeyRepository: repository });

    const tenantAKey = generateApiKey("pk_tenant_a");
    const tenantBKey = generateApiKey("pk_tenant_b");
    const revokedKey = generateApiKey("pk_revoked");
    const expiredRotationKey = generateApiKey("pk_expired_rot");
    const activeRotationKey = generateApiKey("pk_active_rot");

    const tenantAId = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
    const tenantBId = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";

    repository.addRecord({
      publicKeyId: tenantAKey.publicKeyId,
      tenantId: tenantAId,
      secretDigest: tenantAKey.secretDigest,
      createdAt: new Date(),
      lastUsedAt: null,
      revokedAt: null,
      rotationValidUntil: null,
    });

    repository.addRecord({
      publicKeyId: tenantBKey.publicKeyId,
      tenantId: tenantBId,
      secretDigest: tenantBKey.secretDigest,
      createdAt: new Date(),
      lastUsedAt: null,
      revokedAt: null,
      rotationValidUntil: null,
    });

    repository.addRecord({
      publicKeyId: revokedKey.publicKeyId,
      tenantId: tenantAId,
      secretDigest: revokedKey.secretDigest,
      createdAt: new Date(),
      lastUsedAt: null,
      revokedAt: new Date(Date.now() - 60000), // revoked 1 min ago
      rotationValidUntil: null,
    });

    repository.addRecord({
      publicKeyId: expiredRotationKey.publicKeyId,
      tenantId: tenantAId,
      secretDigest: expiredRotationKey.secretDigest,
      createdAt: new Date(),
      lastUsedAt: null,
      revokedAt: null,
      rotationValidUntil: new Date(Date.now() - 1000), // expired 1s ago
    });

    repository.addRecord({
      publicKeyId: activeRotationKey.publicKeyId,
      tenantId: tenantAId,
      secretDigest: activeRotationKey.secretDigest,
      createdAt: new Date(),
      lastUsedAt: null,
      revokedAt: null,
      rotationValidUntil: new Date(Date.now() + 3600000), // valid for 1 more hour
    });

    it("authenticates a valid key and attaches the correct tenant identity (Bearer format)", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${tenantAKey.rawKey}`,
        },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        status: "authenticated",
        tenant_id: tenantAId,
      });
    });

    it("authenticates a valid key for a different tenant and isolates tenant identity", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${tenantBKey.rawKey}`,
        },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        status: "authenticated",
        tenant_id: tenantBId,
      });
    });

    it("authenticates a valid key via raw Authorization header without Bearer prefix", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: tenantAKey.rawKey,
        },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        status: "authenticated",
        tenant_id: tenantAId,
      });
    });

    it("authenticates a valid key via X-API-Key header", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          "x-api-key": tenantAKey.rawKey,
        },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        status: "authenticated",
        tenant_id: tenantAId,
      });
    });

    it("authenticates a key within active rotation window", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${activeRotationKey.rawKey}`,
        },
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        status: "authenticated",
        tenant_id: tenantAId,
      });
    });

    it("rejects missing credentials with generic 401 response", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });

    it("rejects malformed credential formats with generic 401 response", async () => {
      const malformedCases = [
        "Bearer not_a_relay_key",
        "Bearer relay_live_missing_dot",
        "Bearer relay_live_.missing_pk",
        "Bearer relay_live_pk.",
        "Bearer relay_live_pk.secret.extra_dot",
        "Basic dXNlcjpwYXNz",
        "Bearer ",
      ];

      for (const authHeader of malformedCases) {
        const response = await app.inject({
          method: "GET",
          url: "/v1/auth/verify",
          headers: { authorization: authHeader },
        });

        expect(response.statusCode).toBe(401);
        expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
      }
    });

    it("rejects unknown public key identifier with generic 401 response", async () => {
      const unknownKey = "relay_live_pk_unknown_nonexistent.valid_secret_bytes_123456789";
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${unknownKey}`,
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });

    it("rejects incorrect secret for valid public key identifier with generic 401 response", async () => {
      const wrongSecretKey = `relay_live_${tenantAKey.publicKeyId}.wrong_secret_1234567890abcdef`;
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${wrongSecretKey}`,
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });

    it("rejects revoked key with generic 401 response", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${revokedKey.rawKey}`,
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });

    it("rejects key after rotation validity has expired with generic 401 response", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${expiredRotationKey.rawKey}`,
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });

    it("ensures all authentication failure modes produce identical generic 401 responses", async () => {
      const failures = await Promise.all([
        app.inject({ method: "GET", url: "/v1/auth/verify" }), // missing
        app.inject({
          method: "GET",
          url: "/v1/auth/verify",
          headers: { authorization: "Bearer malformed" },
        }), // malformed
        app.inject({
          method: "GET",
          url: "/v1/auth/verify",
          headers: {
            authorization:
              "relay_live_pk_doesnotexist.random_secret_value_12345",
          },
        }), // unknown public id
        app.inject({
          method: "GET",
          url: "/v1/auth/verify",
          headers: {
            authorization: `relay_live_${tenantAKey.publicKeyId}.incorrect_secret_val`,
          },
        }), // wrong secret
        app.inject({
          method: "GET",
          url: "/v1/auth/verify",
          headers: { authorization: revokedKey.rawKey },
        }), // revoked
        app.inject({
          method: "GET",
          url: "/v1/auth/verify",
          headers: { authorization: expiredRotationKey.rawKey },
        }), // expired rotation
      ]);

      for (const res of failures) {
        expect(res.statusCode).toBe(401);
        expect(res.headers["content-type"]).toContain("application/json");
        expect(res.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
      }
    });

    it("ensures caller-supplied query parameters or body cannot spoof or override tenant identity", async () => {
      const response = await app.inject({
        method: "GET",
        url: `/v1/auth/verify?tenant_id=${tenantBId}`,
        headers: {
          authorization: `Bearer ${tenantAKey.rawKey}`,
        },
      });

      expect(response.statusCode).toBe(200);
      // Confirms tenant identity is strictly derived from verified key record, NOT query param
      expect(response.json()).toEqual({
        status: "authenticated",
        tenant_id: tenantAId,
      });
    });

    it("never includes credential material, public key IDs, or secrets in responses", async () => {
      const probeSecret = "probe_secret_unique_canary_value_987654";
      const probeKey = `relay_live_pk_probe_canary.${probeSecret}`;

      const failureRes = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${probeKey}`,
        },
      });

      expect(failureRes.statusCode).toBe(401);
      expect(failureRes.body).not.toContain("probe_secret_unique_canary_value_987654");
      expect(failureRes.body).not.toContain("pk_probe_canary");
      expect(failureRes.body).not.toContain("relay_live");

      const successRes = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${tenantAKey.rawKey}`,
        },
      });

      expect(successRes.statusCode).toBe(200);
      expect(successRes.body).not.toContain(tenantAKey.secret);
      expect(successRes.body).not.toContain(tenantAKey.publicKeyId);
      expect(successRes.body).not.toContain("relay_live");
    });

    it("rejects requests presenting multiple credential headers (both Authorization and X-API-Key)", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${tenantAKey.rawKey}`,
          "x-api-key": tenantAKey.rawKey,
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });

    it("rejects requests presenting multiple conflicting credential headers", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${tenantAKey.rawKey}`,
          "x-api-key": tenantBKey.rawKey,
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });

    it("strictly rejects Bearer credentials with trailing whitespace", async () => {
      const response = await app.inject({
        method: "GET",
        url: "/v1/auth/verify",
        headers: {
          authorization: `Bearer ${tenantAKey.rawKey} `,
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual(GENERIC_UNAUTHORIZED_RESPONSE);
    });
  });
});
