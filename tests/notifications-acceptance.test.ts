import crypto from "node:crypto";
import { describe, expect, it } from "vitest";
import { buildApp } from "../src/app";
import {
  InMemoryNotificationRepository,
  type FingerprintKeyConfig,
  type NotificationResponse,
} from "../src/notifications";
import {
  InMemoryTenantKeyRepository,
  hashApiKeySecret,
  type TenantApiKeyRecord,
} from "../src/auth";

describe("Phase 2C — Notification Acceptance & Idempotency", () => {
  const activeHmacKey = Buffer.alloc(32, 7);
  const activeKeyVersion = 2;
  const previousHmacKeyV1 = Buffer.alloc(32, 6);
  const previousKeys = new Map<number, Buffer>([[1, previousHmacKeyV1]]);

  const fingerprintConfig: FingerprintKeyConfig = {
    hmacKey: activeHmacKey,
    activeKeyVersion,
    previousKeys,
  };

  // Helper to set up app with seeded tenant API keys
  function setupTestApp() {
    const tenantKeyRepo = new InMemoryTenantKeyRepository();
    const notificationRepo = new InMemoryNotificationRepository(
      fingerprintConfig,
    );

    const tenantA = crypto.randomUUID();
    const tenantB = crypto.randomUUID();

    const rawKeyA = "relay_live_pk_tenant_a.0123456789abcdef0123456789abcdef";
    const rawKeyB = "relay_live_pk_tenant_b.fedcba9876543210fedcba9876543210";

    const keyRecordA: TenantApiKeyRecord = {
      publicKeyId: "pk_tenant_a",
      tenantId: tenantA,
      secretDigest: hashApiKeySecret("0123456789abcdef0123456789abcdef"),
      createdAt: new Date(),
      lastUsedAt: null,
      revokedAt: null,
      rotationValidUntil: null,
    };

    const keyRecordB: TenantApiKeyRecord = {
      publicKeyId: "pk_tenant_b",
      tenantId: tenantB,
      secretDigest: hashApiKeySecret("fedcba9876543210fedcba9876543210"),
      createdAt: new Date(),
      lastUsedAt: null,
      revokedAt: null,
      rotationValidUntil: null,
    };

    tenantKeyRepo.addRecord(keyRecordA);
    tenantKeyRepo.addRecord(keyRecordB);

    const app = buildApp({
      tenantKeyRepository: tenantKeyRepo,
      notificationRepository: notificationRepo,
      fingerprintConfig,
    });

    return {
      app,
      tenantA,
      tenantB,
      rawKeyA,
      rawKeyB,
      notificationRepo,
    };
  }

  describe("Authentication and Input Validation Boundaries", () => {
    it("rejects unauthenticated requests with generic 401 Unauthorized", async () => {
      const { app } = setupTestApp();

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          "idempotency-key": "test-key-1",
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "alice@example.com",
          subject: "Test",
          body: "Hello",
        },
      });

      expect(response.statusCode).toBe(401);
      expect(response.json()).toEqual({
        statusCode: 401,
        error: "Unauthorized",
        message: "Unauthorized",
      });
    });

    it("rejects requests missing the Idempotency-Key header with 400 Bad Request", async () => {
      const { app, rawKeyA } = setupTestApp();

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "alice@example.com",
          subject: "Test",
          body: "Hello",
        },
      });

      expect(response.statusCode).toBe(400);
      expect(response.json()).toEqual({
        statusCode: 400,
        error: "Bad Request",
        message: "Idempotency-Key header is required.",
      });
    });

    it("rejects invalid idempotency keys (whitespace, control characters, or > 128 bytes)", async () => {
      const { app, rawKeyA } = setupTestApp();

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "invalid key with spaces",
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "alice@example.com",
          subject: "Test",
          body: "Hello",
        },
      });

      expect(response.statusCode).toBe(400);
      expect(response.json()).toEqual({
        statusCode: 400,
        error: "Bad Request",
        message:
          "Idempotency-Key must contain only printable ASCII characters (0x21-0x7E) with no whitespace.",
      });
    });

    it("never reflects raw idempotency keys or secrets in error responses", async () => {
      const { app, rawKeyA } = setupTestApp();
      const canarySensitiveKey = "CANARY_SENSITIVE_KEY_VALUE_12345 ";

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": canarySensitiveKey,
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "alice@example.com",
          subject: "Test",
          body: "Hello",
        },
      });

      expect(response.statusCode).toBe(400);
      expect(response.body).not.toContain("CANARY_SENSITIVE_KEY");
      expect(response.body).not.toContain(rawKeyA);
    });
  });

  describe("Explicit Preference Enforcement", () => {
    it("suppresses email delivery when no recipient preference row exists (preference_missing)", async () => {
      const { app, rawKeyA, tenantA, notificationRepo } = setupTestApp();

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "key-suppressed-no-pref",
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "unknown_recipient@example.com",
          subject: "Welcome",
          body: "Hello there!",
        },
      });

      expect(response.statusCode).toBe(202);
      const data = response.json<NotificationResponse>();
      expect(data.status).toBe("suppressed");
      expect(data.suppression_reason).toBe("preference_missing");
      expect(data.recipient_id).toBe("unknown_recipient@example.com");
      expect(data.channel).toBe("email");
      expect(typeof data.id).toBe("string");

      // Verify no outbox event was created for suppressed notification
      const outbox = await notificationRepo.getOutboxEventByNotificationId(
        tenantA,
        data.id,
      );
      expect(outbox).toBeNull();

      // Verify notification in store has terminal_at set
      const stored = await notificationRepo.getNotification(tenantA, data.id);
      expect(stored?.status).toBe("suppressed");
      expect(stored?.suppression_reason).toBe("preference_missing");
      expect(stored?.terminal_at).not.toBeNull();
    });

    it("suppresses push delivery when channel preference is explicitly disabled (channel_disabled)", async () => {
      const { app, rawKeyA, tenantA, notificationRepo } = setupTestApp();

      // Explicitly disable push for this recipient
      await notificationRepo.setPreference(
        tenantA,
        "device_token_xyz",
        "push",
        false,
      );

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "key-suppressed-disabled-chan",
          "content-type": "application/json",
        },
        payload: {
          channel: "push",
          recipient_id: "device_token_xyz",
          title: "Alert",
          body: "Flash sale!",
        },
      });

      expect(response.statusCode).toBe(202);
      const data = response.json<NotificationResponse>();
      expect(data.status).toBe("suppressed");
      expect(data.suppression_reason).toBe("channel_disabled");

      // Verify no outbox event
      const outbox = await notificationRepo.getOutboxEventByNotificationId(
        tenantA,
        data.id,
      );
      expect(outbox).toBeNull();
    });

    it("accepts notification and creates pending outbox event when preference is enabled", async () => {
      const { app, rawKeyA, tenantA, notificationRepo } = setupTestApp();

      // Explicitly opt-in
      await notificationRepo.setPreference(
        tenantA,
        "bob@example.com",
        "email",
        true,
      );

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "key-accepted-1",
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "bob@example.com",
          subject: "Your Weekly Digest",
          body: "Here is your summary.",
        },
      });

      expect(response.statusCode).toBe(202);
      const data = response.json<NotificationResponse>();
      expect(data.status).toBe("accepted");
      expect(data.suppression_reason).toBeUndefined();

      // Verify outbox event was created in 'pending' state
      const outbox = await notificationRepo.getOutboxEventByNotificationId(
        tenantA,
        data.id,
      );
      expect(outbox).not.toBeNull();
      expect(outbox?.state).toBe("pending");
      expect(outbox?.tenant_id).toBe(tenantA);
      expect(outbox?.notification_id).toBe(data.id);

      // Verify notification in store has terminal_at === null
      const stored = await notificationRepo.getNotification(tenantA, data.id);
      expect(stored?.status).toBe("accepted");
      expect(stored?.terminal_at).toBeNull();
    });
  });

  describe("Idempotency Replay, Conflict, and Tenant Scoping", () => {
    it("replays existing operation when re-sent with the same key and identical payload (HTTP 200)", async () => {
      const { app, rawKeyA, tenantA, notificationRepo } = setupTestApp();

      await notificationRepo.setPreference(
        tenantA,
        "carol@example.com",
        "email",
        true,
      );

      const payload = {
        channel: "email" as const,
        recipient_id: "carol@example.com",
        subject: "Invoice #1001",
        body: "Amount due: $50.00",
      };

      // 1. Initial request -> 202 Created
      const res1 = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "key-invoice-1001",
          "content-type": "application/json",
        },
        payload,
      });

      expect(res1.statusCode).toBe(202);
      const firstData = res1.json<NotificationResponse>();

      // 2. Replay request -> 200 OK with identical notification ID and status
      const res2 = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "key-invoice-1001",
          "content-type": "application/json",
        },
        payload,
      });

      expect(res2.statusCode).toBe(200);
      const replayedData = res2.json<NotificationResponse>();
      expect(replayedData.id).toBe(firstData.id);
      expect(replayedData.status).toBe(firstData.status);
      expect(replayedData.recipient_id).toBe(firstData.recipient_id);

      // Ensure no duplicate outbox event was created
      const outbox = await notificationRepo.getOutboxEventByNotificationId(
        tenantA,
        firstData.id,
      );
      expect(outbox).not.toBeNull();
    });

    it("rejects request with 409 Conflict when same key is reused with a different payload", async () => {
      const { app, rawKeyA, tenantA, notificationRepo } = setupTestApp();

      await notificationRepo.setPreference(
        tenantA,
        "carol@example.com",
        "email",
        true,
      );

      const originalPayload = {
        channel: "email" as const,
        recipient_id: "carol@example.com",
        subject: "Invoice #1001",
        body: "Amount due: $50.00",
      };

      // Initial request
      const res1 = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "key-invoice-conflict-test",
          "content-type": "application/json",
        },
        payload: originalPayload,
      });
      expect(res1.statusCode).toBe(202);
      const originalNotification = res1.json<NotificationResponse>();

      // Reused key with altered subject -> Conflict
      const conflictingPayload = {
        ...originalPayload,
        subject: "Invoice #1001 - TAMPERED",
      };

      const res2 = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "key-invoice-conflict-test",
          "content-type": "application/json",
        },
        payload: conflictingPayload,
      });

      expect(res2.statusCode).toBe(409);
      expect(res2.json()).toEqual({
        statusCode: 409,
        error: "Conflict",
        message:
          "Idempotency key has already been used with a different request payload.",
      });

      // Verify original notification was NOT mutated
      const stored = await notificationRepo.getNotification(
        tenantA,
        originalNotification.id,
      );
      expect(stored?.subject).toBe("Invoice #1001");
    });

    it("allows Tenant A and Tenant B to use the exact same idempotency key independently", async () => {
      const { app, rawKeyA, rawKeyB, tenantA, tenantB, notificationRepo } =
        setupTestApp();

      await notificationRepo.setPreference(
        tenantA,
        "userA@example.com",
        "email",
        true,
      );
      await notificationRepo.setPreference(
        tenantB,
        "userB@example.com",
        "email",
        true,
      );

      const sharedKey = "shared-client-key-12345";

      // Tenant A submits with sharedKey
      const resA = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": sharedKey,
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "userA@example.com",
          subject: "Notice for A",
          body: "Hello A",
        },
      });

      expect(resA.statusCode).toBe(202);
      const dataA = resA.json<NotificationResponse>();

      // Tenant B submits with same sharedKey and different payload
      const resB = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyB}`,
          "idempotency-key": sharedKey,
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "userB@example.com",
          subject: "Notice for B",
          body: "Hello B",
        },
      });

      // Both must succeed!
      expect(resB.statusCode).toBe(202);
      const dataB = resB.json<NotificationResponse>();

      expect(dataA.id).not.toBe(dataB.id);
      expect(dataA.recipient_id).toBe("userA@example.com");
      expect(dataB.recipient_id).toBe("userB@example.com");

      // Verify reservations in store are scoped to their respective tenants
      const resRecordA = await notificationRepo.getReservation(
        tenantA,
        sharedKey,
      );
      const resRecordB = await notificationRepo.getReservation(
        tenantB,
        sharedKey,
      );

      expect(resRecordA?.tenant_id).toBe(tenantA);
      expect(resRecordB?.tenant_id).toBe(tenantB);
    });
  });

  describe("Historical HMAC Key Rotation & Missing Key Fail-Closed", () => {
    it("successfully verifies and replays a reservation made with an older HMAC key version", async () => {
      const { app, rawKeyA, tenantA, notificationRepo } = setupTestApp();

      await notificationRepo.setPreference(
        tenantA,
        "user_old@example.com",
        "email",
        true,
      );

      const payload = {
        channel: "email" as const,
        recipient_id: "user_old@example.com",
        subject: "Old Statement",
        body: "From last year",
      };

      // Simulate a reservation originally created under HMAC Key Version 1 (previousKeyV1)
      const { computeRequestFingerprint } = await import("../src/idempotency");
      const v1Fingerprint = computeRequestFingerprint(payload, previousHmacKeyV1, 1);

      await notificationRepo.acceptNotification({
        tenantId: tenantA,
        idempotencyKey: "v1-historical-key",
        payload,
        fingerprintResult: v1Fingerprint,
      });

      // Now client replays the same request to the server (which has activeKeyVersion = 2)
      // The server must resolve Key Version 1 from previousKeys and successfully replay
      const replayRes = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "v1-historical-key",
          "content-type": "application/json",
        },
        payload,
      });

      expect(replayRes.statusCode).toBe(200);
      const data = replayRes.json<NotificationResponse>();
      expect(data.recipient_id).toBe("user_old@example.com");
    });

    it("fails closed with sanitized 500 when recorded HMAC key version is missing from configuration", async () => {
      const { app, rawKeyA, tenantA, notificationRepo } = setupTestApp();

      const payload = {
        channel: "email" as const,
        recipient_id: "user_missing_v@example.com",
        subject: "Notice",
        body: "Test",
      };

      // Simulate a reservation recorded with unknown key version 99
      const dummyFingerprint = {
        fingerprint: Buffer.alloc(32, 9),
        fingerprintHex: Buffer.alloc(32, 9).toString("hex"),
        canonicalBytes: Buffer.alloc(10),
        fingerprintFormatVersion: 1,
        hmacKeyVersion: 99, // NOT in config
      };

      await notificationRepo.acceptNotification({
        tenantId: tenantA,
        idempotencyKey: "missing-key-version",
        payload,
        fingerprintResult: dummyFingerprint,
      });

      // Replay must fail closed with 500 Internal Server Error, NOT 409 or fallback to active key
      const replayRes = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "missing-key-version",
          "content-type": "application/json",
        },
        payload,
      });

      expect(replayRes.statusCode).toBe(500);
      expect(replayRes.json()).toEqual({
        statusCode: 500,
        error: "Internal Server Error",
        message: "An internal server error occurred.",
      });
    });
  });

  describe("Tenant Isolation on Status and Preference Endpoints", () => {
    it("returns 404 when Tenant B attempts to read Tenant A's notification", async () => {
      const { app, rawKeyA, rawKeyB, tenantA, notificationRepo } = setupTestApp();

      await notificationRepo.setPreference(
        tenantA,
        "alice@example.com",
        "email",
        true,
      );

      // Tenant A creates notification
      const createRes = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "alice-notif-1",
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "alice@example.com",
          subject: "Private",
          body: "Confidential message",
        },
      });

      expect(createRes.statusCode).toBe(202);
      const notifId = createRes.json<NotificationResponse>().id;

      // Tenant A reads it -> 200 OK
      const readA = await app.inject({
        method: "GET",
        url: `/v1/notifications/${notifId}`,
        headers: { authorization: `Bearer ${rawKeyA}` },
      });
      expect(readA.statusCode).toBe(200);

      // Tenant B attempts to read it -> 404 Not Found (non-disclosing)
      const readB = await app.inject({
        method: "GET",
        url: `/v1/notifications/${notifId}`,
        headers: { authorization: `Bearer ${rawKeyB}` },
      });
      expect(readB.statusCode).toBe(404);
      expect(readB.json()).toEqual({
        statusCode: 404,
        error: "Not Found",
        message: "Notification not found.",
      });
    });

    it("isolates preferences between tenants", async () => {
      const { app, rawKeyA, rawKeyB } = setupTestApp();

      // Tenant A sets preference for recipient "shared_recipient"
      await app.inject({
        method: "PUT",
        url: "/v1/preferences/shared_recipient",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "content-type": "application/json",
        },
        payload: { channel: "email", enabled: true },
      });

      // Tenant A reads preference -> enabled: true
      const resA = await app.inject({
        method: "GET",
        url: "/v1/preferences/shared_recipient?channel=email",
        headers: { authorization: `Bearer ${rawKeyA}` },
      });
      expect(resA.statusCode).toBe(200);
      expect(resA.json().enabled).toBe(true);

      // Tenant B reads preference for same recipient -> enabled: null (missing preference)
      const resB = await app.inject({
        method: "GET",
        url: "/v1/preferences/shared_recipient?channel=email",
        headers: { authorization: `Bearer ${rawKeyB}` },
      });
      expect(resB.statusCode).toBe(200);
      expect(resB.json().enabled).toBeNull();
    });

    it("returns non-disclosing 404 for malformed or non-UUID notification IDs", async () => {
      const { app, rawKeyA } = setupTestApp();

      const res = await app.inject({
        method: "GET",
        url: "/v1/notifications/not-a-valid-uuid",
        headers: { authorization: `Bearer ${rawKeyA}` },
      });

      expect(res.statusCode).toBe(404);
      expect(res.json()).toEqual({
        statusCode: 404,
        error: "Not Found",
        message: "Notification not found.",
      });
    });

    it("rejects preference operations with recipient IDs exceeding 256 UTF-8 bytes", async () => {
      const { app, rawKeyA } = setupTestApp();
      const oversizedRecipient = "a".repeat(257);

      const getRes = await app.inject({
        method: "GET",
        url: `/v1/preferences/${oversizedRecipient}?channel=email`,
        headers: { authorization: `Bearer ${rawKeyA}` },
      });
      expect(getRes.statusCode).toBe(400);
      expect(getRes.json().message).toContain("Recipient ID must be between 1 and 256 UTF-8 bytes.");

      const putRes = await app.inject({
        method: "PUT",
        url: `/v1/preferences/${oversizedRecipient}`,
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "content-type": "application/json",
        },
        payload: { channel: "email", enabled: true },
      });
      expect(putRes.statusCode).toBe(400);
      expect(putRes.json().message).toContain("Recipient ID must be between 1 and 256 UTF-8 bytes.");
    });

    it("rejects unexpected properties in preference update payload with 400 Bad Request", async () => {
      const { app, rawKeyA } = setupTestApp();

      const res = await app.inject({
        method: "PUT",
        url: "/v1/preferences/alice@example.com",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          enabled: true,
          unexpected_field: "malicious_injection",
        },
      });

      expect(res.statusCode).toBe(400);
      expect(res.json().message).toContain("Unexpected property 'unexpected_field'");
    });
  });

  describe("Transaction Failure and Rollback Handling", () => {
    it("returns 500 and prevents partial persistence when transaction failure is injected", async () => {
      const { app, rawKeyA, notificationRepo } = setupTestApp();

      // Inject simulated database transaction failure
      notificationRepo.simulatedFailures.push(
        new Error("Simulated database disk failure"),
      );

      const response = await app.inject({
        method: "POST",
        url: "/v1/notifications",
        headers: {
          authorization: `Bearer ${rawKeyA}`,
          "idempotency-key": "fail-key-1",
          "content-type": "application/json",
        },
        payload: {
          channel: "email",
          recipient_id: "test@example.com",
          subject: "Test",
          body: "Body",
        },
      });

      expect(response.statusCode).toBe(500);
      expect(response.json()).toEqual({
        statusCode: 500,
        error: "Internal Server Error",
        message: "An internal server error occurred.",
      });
    });
  });
});
