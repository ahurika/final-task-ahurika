import crypto from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import type { Pool, PoolClient, QueryResult } from "pg";
import {
  PostgresNotificationRepository,
  isRetryableTransactionError,
  isIdempotencyKeyConflict,
  MAX_TRANSACTION_ATTEMPTS,
  TRANSACTION_RETRY_DELAYS_MS,
  RESERVATION_RETENTION_DAYS,
  type FingerprintKeyConfig,
} from "../src/notifications";
import { MissingHmacKeyVersionError } from "../src/notifications/types";
import { computeRequestFingerprint } from "../src/idempotency";
import type { NotificationPayload } from "../src/validation";

describe("Phase 2C — PostgresNotificationRepository (Mocked Transaction Unit Tests)", () => {
  const activeHmacKey = Buffer.alloc(32, 8);
  const activeKeyVersion = 2;
  const previousHmacKeyV1 = Buffer.alloc(32, 5);
  const previousKeys = new Map<number, Buffer>([[1, previousHmacKeyV1]]);

  const fingerprintConfig: FingerprintKeyConfig = {
    hmacKey: activeHmacKey,
    activeKeyVersion,
    previousKeys,
  };

  const sampleEmailPayload: NotificationPayload = {
    channel: "email",
    recipient_id: "test@example.com",
    subject: "Meeting Notice",
    body: "Please attend tomorrow.",
  };

  const sampleFingerprint = computeRequestFingerprint(
    sampleEmailPayload,
    activeHmacKey,
    activeKeyVersion,
  );

  // Helper to create mock pool client
  function createMockClient() {
    const executedQueries: Array<{ text: string; values?: unknown[] }> = [];

    const mockClient = {
      query: vi.fn(async (textOrConfig: string | { text: string; values?: unknown[] }, values?: unknown[]) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;
        const vals = typeof textOrConfig === "string" ? values : textOrConfig.values;
        executedQueries.push({ text: text.trim(), values: vals });

        // Default query responses based on SQL command
        if (text.includes("notification_preferences")) {
          return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
        }

        return { rows: [], rowCount: 1 } as unknown as QueryResult;
      }),
      release: vi.fn(),
    };

    return { mockClient, executedQueries };
  }

  function createMockPool(mockClient: unknown) {
    return {
      connect: vi.fn(async () => mockClient as PoolClient),
      query: vi.fn(async () => ({ rows: [], rowCount: 0 } as unknown as QueryResult)),
    } as unknown as Pool;
  }

  describe("Transaction Boundaries and Statement Timeouts", () => {
    it("executes BEGIN, SET LOCAL timeouts, INSERTs, and COMMIT within a single transaction", async () => {
      const { mockClient, executedQueries } = createMockClient();
      const mockPool = createMockPool(mockClient);

      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      const result = await repo.acceptNotification({
        tenantId: "tenant-123",
        idempotencyKey: "key-tx-boundary-1",
        payload: sampleEmailPayload,
        fingerprintResult: sampleFingerprint,
      });

      expect(result.outcome).toBe("created");
      expect(result.notification?.status).toBe("accepted");

      // Verify transaction flow:
      // 1. BEGIN
      // 2. SET LOCAL lock_timeout = '5s'
      // 3. SET LOCAL statement_timeout = '5s'
      // 4. SELECT preference
      // 5. INSERT notifications
      // 6. INSERT idempotency_reservations
      // 7. INSERT outbox_events
      // 8. COMMIT
      expect(executedQueries[0].text).toBe("BEGIN;");
      expect(executedQueries[1].text).toBe("SET LOCAL lock_timeout = '5s';");
      expect(executedQueries[2].text).toBe("SET LOCAL statement_timeout = '5s';");
      expect(executedQueries[3].text).toContain("SELECT enabled");
      expect(executedQueries[4].text).toContain("INSERT INTO notifications");
      expect(executedQueries[5].text).toContain("INSERT INTO idempotency_reservations");
      expect(executedQueries[6].text).toContain("INSERT INTO outbox_events");
      expect(executedQueries[7].text).toBe("COMMIT;");

      expect(mockClient.release).toHaveBeenCalledTimes(1);
    });

    it("rolls back the transaction and releases client when an insert fails", async () => {
      const { mockClient, executedQueries } = createMockClient();

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;
        executedQueries.push({ text: text.trim() });

        if (text.includes("INSERT INTO notifications")) {
          throw new Error("Disk I/O error during notification insert");
        }
        return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = createMockPool(mockClient);
      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      await expect(
        repo.acceptNotification({
          tenantId: "tenant-123",
          idempotencyKey: "key-fail-rollback",
          payload: sampleEmailPayload,
          fingerprintResult: sampleFingerprint,
        }),
      ).rejects.toThrow("Disk I/O error during notification insert");

      // Verify ROLLBACK was executed
      const rollbackExecuted = executedQueries.some((q) => q.text.includes("ROLLBACK;"));
      expect(rollbackExecuted).toBe(true);
      expect(mockClient.release).toHaveBeenCalledTimes(1);
    });
  });

  describe("Retryable Transaction Error Classification and Bounded Retries", () => {
    it("classifies PostgreSQL SQLSTATEs 40001, 40P01, 55P03, and 57014 as retryable", () => {
      expect(isRetryableTransactionError({ code: "40001" })).toBe(true); // serialization_failure
      expect(isRetryableTransactionError({ code: "40P01" })).toBe(true); // deadlock_detected
      expect(isRetryableTransactionError({ code: "55P03" })).toBe(true); // lock_not_available
      expect(isRetryableTransactionError({ code: "57014" })).toBe(true); // query_canceled / statement_timeout

      // Non-retryable errors
      expect(isRetryableTransactionError({ code: "23505" })).toBe(false); // unique_violation
      expect(isRetryableTransactionError({ code: "42P01" })).toBe(false); // undefined_table
      expect(isRetryableTransactionError({ code: "28000" })).toBe(false); // invalid_auth
      expect(isRetryableTransactionError(new Error("Generic"))).toBe(false);
      expect(isRetryableTransactionError(null)).toBe(false);
    });

    it("retries transient transaction failures up to 3 times with deterministic delays (100ms, 250ms)", async () => {
      const { mockClient } = createMockClient();
      let attemptCount = 0;
      const sleepDelays: number[] = [];

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;
        if (text.includes("INSERT INTO notifications")) {
          attemptCount++;
          if (attemptCount < 3) {
            // Throw deadlock error on attempts 1 and 2
            const deadlockError = new Error("Deadlock detected");
            (deadlockError as { code?: string }).code = "40P01";
            throw deadlockError;
          }
        }
        return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = createMockPool(mockClient);
      const mockSleep = async (ms: number) => {
        sleepDelays.push(ms);
      };

      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig, mockSleep);

      const result = await repo.acceptNotification({
        tenantId: "tenant-123",
        idempotencyKey: "key-retry-success",
        payload: sampleEmailPayload,
        fingerprintResult: sampleFingerprint,
      });

      expect(result.outcome).toBe("created");
      expect(attemptCount).toBe(3);
      expect(sleepDelays).toEqual(TRANSACTION_RETRY_DELAYS_MS); // [100, 250]
    });

    it("fails when retryable errors exceed the 3-attempt limit", async () => {
      const { mockClient } = createMockClient();
      const sleepDelays: number[] = [];

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;
        if (text.includes("INSERT INTO notifications")) {
          const timeoutError = new Error("Statement timeout");
          (timeoutError as { code?: string }).code = "57014";
          throw timeoutError;
        }
        return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = createMockPool(mockClient);
      const mockSleep = async (ms: number) => {
        sleepDelays.push(ms);
      };

      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig, mockSleep);

      await expect(
        repo.acceptNotification({
          tenantId: "tenant-123",
          idempotencyKey: "key-retry-exhausted",
          payload: sampleEmailPayload,
          fingerprintResult: sampleFingerprint,
        }),
      ).rejects.toThrow("Statement timeout");

      expect(sleepDelays).toEqual([100, 250]);
    });
  });

  describe("Concurrency Winner Resolution & Fresh Statement Snapshot", () => {
    it("handles 23505 unique conflict by rolling back and reading committed winner in fresh query", async () => {
      const { mockClient, executedQueries } = createMockClient();

      const existingWinnerId = crypto.randomUUID();

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;
        executedQueries.push({ text: text.trim() });

        if (text.includes("INSERT INTO idempotency_reservations")) {
          // Simulate losing candidate: concurrent transaction already committed this key
          const uniqueErr = new Error("duplicate key value violates unique constraint");
          (uniqueErr as { code?: string; constraint?: string }).code = "23505";
          (uniqueErr as { code?: string; constraint?: string }).constraint =
            "idempotency_reservations_tenant_id_idempotency_key_key";
          throw uniqueErr;
        }

        if (text.includes("FROM idempotency_reservations r")) {
          // Fresh snapshot query returns the committed winner
          return {
            rows: [
              {
                notification_id: existingWinnerId,
                status: "accepted",
                channel: "email",
                recipient_id: "test@example.com",
                suppression_reason: null,
                accepted_at: new Date("2026-10-23T08:00:00Z"),
                request_fingerprint: sampleFingerprint.fingerprint,
                fingerprint_format_version: 1,
                hmac_key_version: 2,
              },
            ],
            rowCount: 1,
          } as unknown as QueryResult;
        }

        return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = createMockPool(mockClient);
      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      const result = await repo.acceptNotification({
        tenantId: "tenant-123",
        idempotencyKey: "key-race-winner",
        payload: sampleEmailPayload,
        fingerprintResult: sampleFingerprint,
      });

      expect(result.outcome).toBe("replayed");
      expect(result.notification?.id).toBe(existingWinnerId);

      // Verify that candidate transaction rolled back BEFORE fresh winner lookup
      const rollbackIndex = executedQueries.findIndex((q) => q.text.includes("ROLLBACK;"));
      const winnerQueryIndex = executedQueries.findIndex((q) =>
        q.text.includes("FROM idempotency_reservations r"),
      );
      expect(rollbackIndex).toBeGreaterThan(-1);
      expect(winnerQueryIndex).toBeGreaterThan(rollbackIndex);
    });

    it("returns conflict when winner fingerprint does not match replayed payload", async () => {
      const { mockClient } = createMockClient();

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;

        if (text.includes("INSERT INTO idempotency_reservations")) {
          const uniqueErr = new Error("duplicate key value violates unique constraint");
          (uniqueErr as { code?: string; constraint?: string }).code = "23505";
          (uniqueErr as { code?: string; constraint?: string }).constraint =
            "idempotency_reservations_tenant_id_idempotency_key_key";
          throw uniqueErr;
        }

        if (text.includes("FROM idempotency_reservations r")) {
          return {
            rows: [
              {
                notification_id: crypto.randomUUID(),
                status: "accepted",
                channel: "email",
                recipient_id: "test@example.com",
                suppression_reason: null,
                accepted_at: new Date(),
                // Different fingerprint in winner row
                request_fingerprint: Buffer.alloc(32, 99),
                fingerprint_format_version: 1,
                hmac_key_version: 2,
              },
            ],
            rowCount: 1,
          } as unknown as QueryResult;
        }

        return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = createMockPool(mockClient);
      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      const result = await repo.acceptNotification({
        tenantId: "tenant-123",
        idempotencyKey: "key-race-conflict",
        payload: sampleEmailPayload,
        fingerprintResult: sampleFingerprint,
      });

      expect(result.outcome).toBe("conflict");
      expect(result.notification).toBeUndefined();
    });

    it("fails closed when winner HMAC key version is not configured", async () => {
      const { mockClient } = createMockClient();

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;

        if (text.includes("INSERT INTO idempotency_reservations")) {
          const uniqueErr = new Error("duplicate key value violates unique constraint");
          (uniqueErr as { code?: string; constraint?: string }).code = "23505";
          (uniqueErr as { code?: string; constraint?: string }).constraint =
            "idempotency_reservations_tenant_id_idempotency_key_key";
          throw uniqueErr;
        }

        if (text.includes("FROM idempotency_reservations r")) {
          return {
            rows: [
              {
                notification_id: crypto.randomUUID(),
                status: "accepted",
                channel: "email",
                recipient_id: "test@example.com",
                suppression_reason: null,
                accepted_at: new Date(),
                request_fingerprint: sampleFingerprint.fingerprint,
                fingerprint_format_version: 1,
                hmac_key_version: 999, // Unknown key version
              },
            ],
            rowCount: 1,
          } as unknown as QueryResult;
        }

        return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = createMockPool(mockClient);
      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      await expect(
        repo.acceptNotification({
          tenantId: "tenant-123",
          idempotencyKey: "key-unknown-version",
          payload: sampleEmailPayload,
          fingerprintResult: sampleFingerprint,
        }),
      ).rejects.toThrow(MissingHmacKeyVersionError);
    });

    it("correctly parses hex-encoded BYTEA strings (\\x...) returned from PostgreSQL", async () => {
      const { mockClient } = createMockClient();
      const existingWinnerId = crypto.randomUUID();

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;

        if (text.includes("INSERT INTO idempotency_reservations")) {
          const uniqueErr = new Error("duplicate key value violates unique constraint");
          (uniqueErr as { code?: string; constraint?: string }).code = "23505";
          (uniqueErr as { code?: string; constraint?: string }).constraint =
            "idempotency_reservations_tenant_id_idempotency_key_key";
          throw uniqueErr;
        }

        if (text.includes("FROM idempotency_reservations r")) {
          return {
            rows: [
              {
                notification_id: existingWinnerId,
                status: "accepted",
                channel: "email",
                recipient_id: "test@example.com",
                suppression_reason: null,
                accepted_at: new Date(),
                // Returned as hex string with \x prefix from pg driver
                request_fingerprint: `\\x${sampleFingerprint.fingerprint.toString("hex")}`,
                fingerprint_format_version: 1,
                hmac_key_version: 2,
              },
            ],
            rowCount: 1,
          } as unknown as QueryResult;
        }

        return { rows: [{ enabled: true }], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = createMockPool(mockClient);
      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      const result = await repo.acceptNotification({
        tenantId: "tenant-123",
        idempotencyKey: "key-hex-bytea",
        payload: sampleEmailPayload,
        fingerprintResult: sampleFingerprint,
      });

      expect(result.outcome).toBe("replayed");
      expect(result.notification?.id).toBe(existingWinnerId);
    });
  });
});
