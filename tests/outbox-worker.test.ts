import crypto from "node:crypto";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import type { Pool, PoolClient, QueryResult } from "pg";
import {
  DeliveryWorker,
  calculateRetryDelayMs,
  MAX_DELIVERY_ATTEMPTS,
  RETRY_DELAYS_MS,
  type Clock,
} from "../src/worker";
import {
  FakeEmailProviderAdapter,
  FakePushProviderAdapter,
} from "../src/providers";
import {
  InMemoryNotificationRepository,
  PostgresNotificationRepository,
  type FingerprintKeyConfig,
} from "../src/notifications";
import { computeRequestFingerprint } from "../src/idempotency";
import type { NotificationPayload } from "../src/validation";

describe("Phase 3 — Outbox Worker & Asynchronous Delivery Engine", () => {
  const hmacKey = Buffer.alloc(32, 9);
  const fingerprintConfig: FingerprintKeyConfig = {
    hmacKey,
    activeKeyVersion: 1,
    previousKeys: new Map(),
  };

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  class TestClock implements Clock {
    private currentTime: number;

    constructor(initialTime = new Date("2026-10-23T12:00:00Z").getTime()) {
      this.currentTime = initialTime;
      vi.setSystemTime(this.currentTime);
    }

    now(): Date {
      return new Date(this.currentTime);
    }

    advance(ms: number): void {
      this.currentTime += ms;
      vi.setSystemTime(this.currentTime);
    }

    async sleep(ms: number): Promise<void> {
      this.advance(ms);
    }
  }

  function setupTestEnvironment() {
    const clock = new TestClock();
    const repository = new InMemoryNotificationRepository(fingerprintConfig);
    const emailAdapter = new FakeEmailProviderAdapter({ initialMode: "success" });
    const pushAdapter = new FakePushProviderAdapter({ initialMode: "success" });

    const worker = new DeliveryWorker({
      repository,
      emailAdapter,
      pushAdapter,
      batchSize: 10,
      leaseDurationMs: 30000,
      clock,
    });

    return {
      clock,
      repository,
      emailAdapter,
      pushAdapter,
      worker,
    };
  }

  async function acceptSampleNotification(
    repo: InMemoryNotificationRepository,
    tenantId: string,
    channel: "email" | "push",
    recipientId: string,
    key: string,
  ) {
    await repo.setPreference(tenantId, recipientId, channel, true);

    const payload: NotificationPayload =
      channel === "email"
        ? {
            channel: "email",
            recipient_id: recipientId,
            subject: "Quarterly Report",
            body: "Your metrics are ready.",
          }
        : {
            channel: "push",
            recipient_id: recipientId,
            title: "Security Alert",
            body: "New login detected.",
          };

    const fingerprint = computeRequestFingerprint(payload, hmacKey, 1);
    const result = await repo.acceptNotification({
      tenantId,
      idempotencyKey: key,
      payload,
      fingerprintResult: fingerprint,
    });

    return { result, payload };
  }

  describe("Exponential Backoff Calculation", () => {
    it("matches exact backoff schedule (1s, 2s, 4s, 8s) capped at 30s", () => {
      expect(calculateRetryDelayMs(1)).toBe(1000);
      expect(calculateRetryDelayMs(2)).toBe(2000);
      expect(calculateRetryDelayMs(3)).toBe(4000);
      expect(calculateRetryDelayMs(4)).toBe(8000);
      expect(calculateRetryDelayMs(5)).toBe(8000); // capped at max defined schedule
      expect(RETRY_DELAYS_MS).toEqual([1000, 2000, 4000, 8000]);
    });
  });

  describe("Asynchronous Dispatch and Success Lifecycle (FR-03, FR-06)", () => {
    it("asynchronously claims pending email outbox work and delivers through fake email adapter", async () => {
      const { repository, emailAdapter, worker } = setupTestEnvironment();
      const tenantId = crypto.randomUUID();

      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "alice@example.com",
        "key-async-email",
      );

      const notifId = result.notification!.id;

      // Acceptance created durable records: notification 'accepted', outbox 'pending'
      const initialNotif = await repository.getNotification(tenantId, notifId);
      expect(initialNotif?.status).toBe("accepted");
      expect(initialNotif?.terminal_at).toBeNull();
      expect(initialNotif?.attempt_count).toBe(0);

      const initialOutbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(initialOutbox?.state).toBe("pending");
      expect(initialOutbox?.completed_at).toBeNull();

      // Worker executes batch poll and delivery
      const runResult = await worker.processBatch();
      expect(runResult.claimedCount).toBe(1);
      expect(runResult.succeededCount).toBe(1);

      // Verify delivered state
      const completedNotif = await repository.getNotification(tenantId, notifId);
      expect(completedNotif?.status).toBe("sent");
      expect(completedNotif?.terminal_at).toBeDefined();
      expect(completedNotif?.attempt_count).toBe(1);

      const completedOutbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(completedOutbox?.state).toBe("completed");
      expect(completedOutbox?.completed_at).toBeDefined();

      // Verify attempt history
      const attempts = await repository.getDeliveryAttempts(tenantId, notifId);
      expect(attempts).toHaveLength(1);
      expect(attempts[0].attempt_number).toBe(1);
      expect(attempts[0].state).toBe("provider_accepted");
      expect(attempts[0].completed_at).toBeDefined();

      expect(emailAdapter.sentPayloads).toHaveLength(1);
      expect(emailAdapter.sentPayloads[0].recipientId).toBe("alice@example.com");
      expect(emailAdapter.sentPayloads[0].subject).toBe("Quarterly Report");
    });

    it("asynchronously claims pending push outbox work and delivers through fake push adapter", async () => {
      const { repository, pushAdapter, worker } = setupTestEnvironment();
      const tenantId = crypto.randomUUID();

      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "push",
        "device_token_xyz",
        "key-async-push",
      );

      const notifId = result.notification!.id;

      const runResult = await worker.processBatch();
      expect(runResult.claimedCount).toBe(1);
      expect(runResult.succeededCount).toBe(1);

      const completedNotif = await repository.getNotification(tenantId, notifId);
      expect(completedNotif?.status).toBe("sent");
      expect(completedNotif?.attempt_count).toBe(1);

      const attempts = await repository.getDeliveryAttempts(tenantId, notifId);
      expect(attempts).toHaveLength(1);
      expect(attempts[0].state).toBe("provider_accepted");

      expect(pushAdapter.sentPayloads).toHaveLength(1);
      expect(pushAdapter.sentPayloads[0].title).toBe("Security Alert");
    });
  });

  describe("Pre-send Preference Check Immediately Before Provider Invocation", () => {
    it("suppresses delivery if recipient preference is revoked (disabled) after acceptance", async () => {
      const { repository, emailAdapter, worker } = setupTestEnvironment();
      const tenantId = crypto.randomUUID();

      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "charlie@example.com",
        "key-revoke-test",
      );

      const notifId = result.notification!.id;

      // Recipient opts out while notification is queued in outbox
      await repository.setPreference(
        tenantId,
        "charlie@example.com",
        "email",
        false,
      );

      // Worker runs pre-send check immediately before provider call
      const runResult = await worker.processBatch();
      expect(runResult.claimedCount).toBe(1);
      expect(runResult.suppressedCount).toBe(1);
      expect(runResult.succeededCount).toBe(0);

      // Notification is suppressed with channel_disabled
      const notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("suppressed");
      expect(notif?.suppression_reason).toBe("channel_disabled");
      expect(notif?.terminal_at).toBeDefined();

      // Outbox item is marked completed
      const outbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(outbox?.state).toBe("completed");

      // Zero provider calls and zero attempt rows inserted
      expect(emailAdapter.sentPayloads).toHaveLength(0);
      const attempts = await repository.getDeliveryAttempts(tenantId, notifId);
      expect(attempts).toHaveLength(0);
    });
  });

  describe("Permanent Failure Handling (FR-08)", () => {
    it("transitions directly to terminal 'failed' on permanent provider failure with no retries", async () => {
      const { repository, emailAdapter, worker } = setupTestEnvironment();
      emailAdapter.setMode("permanent_failure");

      const tenantId = crypto.randomUUID();
      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "bad-recipient@example.com",
        "key-perm-fail",
      );

      const notifId = result.notification!.id;

      const runResult = await worker.processBatch();
      expect(runResult.claimedCount).toBe(1);
      expect(runResult.failedCount).toBe(1);

      const notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("failed");
      expect(notif?.terminal_at).toBeDefined();
      expect(notif?.attempt_count).toBe(1);

      const outbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(outbox?.state).toBe("completed");

      const attempts = await repository.getDeliveryAttempts(tenantId, notifId);
      expect(attempts).toHaveLength(1);
      expect(attempts[0].state).toBe("permanent_failure");
      expect(attempts[0].failure_code).toBe("INVALID_EMAIL_ADDRESS");

      // Second worker run finds no pending work
      const secondRun = await worker.processBatch();
      expect(secondRun.claimedCount).toBe(0);
    });
  });

  describe("Ambiguous Provider Timeout (Duplicate Prevention)", () => {
    it("transitions to terminal 'delivery_unknown' and never automatically retries a timed-out call", async () => {
      const { repository, emailAdapter, worker } = setupTestEnvironment();
      emailAdapter.setMode("timeout");

      const tenantId = crypto.randomUUID();
      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "timeout-recipient@example.com",
        "key-timeout-test",
      );

      const notifId = result.notification!.id;

      const runResult = await worker.processBatch();
      expect(runResult.claimedCount).toBe(1);
      expect(runResult.timeoutCount).toBe(1);

      // Notification enters delivery_unknown (requires review; terminal for automatic processing)
      const notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("delivery_unknown");
      expect(notif?.terminal_at).toBeDefined();
      expect(notif?.attempt_count).toBe(1);

      const outbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(outbox?.state).toBe("completed");

      const attempts = await repository.getDeliveryAttempts(tenantId, notifId);
      expect(attempts).toHaveLength(1);
      expect(attempts[0].state).toBe("timeout_ambiguous");
      expect(attempts[0].failure_code).toBe("PROVIDER_GATEWAY_TIMEOUT");

      // Critical constraint: never automatically retried
      const nextRun = await worker.processBatch();
      expect(nextRun.claimedCount).toBe(0);
    });
  });

  describe("Bounded Exponential Backoff and Attempt History (FR-07, FR-10)", () => {
    it("reschedules transient failures with exact backoff delays and recovers when provider succeeds", async () => {
      const { clock, repository, emailAdapter, worker } = setupTestEnvironment();

      // Sequence: fails attempt 1 and 2, succeeds on attempt 3
      emailAdapter.setSequence([
        {
          outcome: "retryable_failure",
          failureCode: "PROVIDER_503_UNAVAILABLE",
        },
        {
          outcome: "retryable_failure",
          failureCode: "PROVIDER_503_UNAVAILABLE",
        },
        {
          outcome: "provider_accepted",
          providerMessageId: "recovery-accepted",
        },
      ]);

      const tenantId = crypto.randomUUID();
      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "retry-user@example.com",
        "key-retry-recovery",
      );

      const notifId = result.notification!.id;

      // Attempt 1
      const run1 = await worker.processBatch();
      expect(run1.retriedCount).toBe(1);

      let notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("retry_scheduled");
      expect(notif?.attempt_count).toBe(1);

      let outbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(outbox?.state).toBe("pending");
      // Backoff delay for attempt 1 is 1000ms
      expect(outbox?.available_at.getTime()).toBe(clock.now().getTime() + 1000);

      // Immediate poll before available_at finds 0 eligible items
      const prematureRun = await worker.processBatch();
      expect(prematureRun.claimedCount).toBe(0);

      // Advance clock by 1001ms
      clock.advance(1001);

      // Attempt 2
      const run2 = await worker.processBatch();
      expect(run2.retriedCount).toBe(1);

      notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("retry_scheduled");
      expect(notif?.attempt_count).toBe(2);

      outbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      // Backoff delay for attempt 2 is 2000ms
      expect(outbox?.available_at.getTime()).toBe(clock.now().getTime() + 2000);

      // Advance clock by 2001ms
      clock.advance(2001);

      // Attempt 3 (Succeeds)
      const run3 = await worker.processBatch();
      expect(run3.succeededCount).toBe(1);

      notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("sent");
      expect(notif?.attempt_count).toBe(3);
      expect(notif?.terminal_at).toBeDefined();

      outbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(outbox?.state).toBe("completed");

      // Verify append-only attempt history: exactly 3 distinct rows
      const attempts = await repository.getDeliveryAttempts(tenantId, notifId);
      expect(attempts).toHaveLength(3);
      expect(attempts[0].attempt_number).toBe(1);
      expect(attempts[0].state).toBe("retryable_failure");
      expect(attempts[1].attempt_number).toBe(2);
      expect(attempts[1].state).toBe("retryable_failure");
      expect(attempts[2].attempt_number).toBe(3);
      expect(attempts[2].state).toBe("provider_accepted");
    });

    it("exhausts retry budget and transitions to terminal 'failed' after 5 failed attempts", async () => {
      const { clock, repository, emailAdapter, worker } = setupTestEnvironment();
      emailAdapter.setMode("transient_failure");

      const tenantId = crypto.randomUUID();
      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "exhaust-user@example.com",
        "key-retry-exhaust",
      );

      const notifId = result.notification!.id;

      // Run through attempts 1, 2, 3, 4
      for (let attempt = 1; attempt <= 4; attempt++) {
        const run = await worker.processBatch();
        expect(run.retriedCount).toBe(1);

        const notif = await repository.getNotification(tenantId, notifId);
        expect(notif?.status).toBe("retry_scheduled");
        expect(notif?.attempt_count).toBe(attempt);

        const delayMs = calculateRetryDelayMs(attempt);
        clock.advance(delayMs + 1);
      }

      // Attempt 5 (Fifth and final attempt)
      const finalRun = await worker.processBatch();
      expect(finalRun.failedCount).toBe(1);
      expect(finalRun.retriedCount).toBe(0);

      const notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("failed");
      expect(notif?.attempt_count).toBe(MAX_DELIVERY_ATTEMPTS);
      expect(notif?.terminal_at).toBeDefined();

      const outbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(outbox?.state).toBe("completed");

      // Verify all 5 attempts recorded
      const attempts = await repository.getDeliveryAttempts(tenantId, notifId);
      expect(attempts).toHaveLength(5);
      attempts.forEach((att, idx) => {
        expect(att.attempt_number).toBe(idx + 1);
        expect(att.state).toBe("retryable_failure");
      });
    });
  });

  describe("Lease Recovery and Stale Worker Fencing", () => {
    it("safely reclaims outbox work if worker crashes and lease expires", async () => {
      const { clock, repository, emailAdapter, worker } = setupTestEnvironment();
      const tenantId = crypto.randomUUID();

      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "crash-recovery@example.com",
        "key-crash-test",
      );

      const notifId = result.notification!.id;

      // 1. Worker claims work
      const claimed = await repository.claimOutboxWork({
        batchSize: 1,
        leaseDurationMs: 30000,
        now: clock.now(),
      });
      expect(claimed).toHaveLength(1);

      const outboxClaimed = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(outboxClaimed?.state).toBe("claimed");

      // 2. Simulate worker crash: worker does not finish.
      // Clock advances 30001ms past lease expiry.
      clock.advance(30001);

      // 3. New worker iteration reclaims expired lease
      const runResult = await worker.processBatch();
      expect(runResult.claimedCount).toBe(1);
      expect(runResult.succeededCount).toBe(1);

      const completedNotif = await repository.getNotification(tenantId, notifId);
      expect(completedNotif?.status).toBe("sent");

      const completedOutbox = await repository.getOutboxEventByNotificationId(
        tenantId,
        notifId,
      );
      expect(completedOutbox?.state).toBe("completed");
    });

    it("rejects state commit from stale worker using optimistic lease fencing", async () => {
      const { clock, repository } = setupTestEnvironment();
      const tenantId = crypto.randomUUID();

      const { result } = await acceptSampleNotification(
        repository,
        tenantId,
        "email",
        "fence-user@example.com",
        "key-fence-test",
      );

      const notifId = result.notification!.id;

      // Worker 1 claims item
      const claimedW1 = await repository.claimOutboxWork({
        batchSize: 1,
        leaseDurationMs: 30000,
        now: clock.now(),
      });
      const workItem1 = claimedW1[0];

      // Worker 1 hangs/pauses. Clock advances past lease expiry.
      clock.advance(31000);

      // Worker 2 claims the item
      const claimedW2 = await repository.claimOutboxWork({
        batchSize: 1,
        leaseDurationMs: 30000,
        now: clock.now(),
      });
      const workItem2 = claimedW2[0];
      expect(workItem2.outboxId).toBe(workItem1.outboxId);
      expect(workItem2.fenceToken.getTime()).not.toBe(
        workItem1.fenceToken.getTime(),
      );

      // Stale Worker 1 wakes up and attempts to commit result with its expired fenceToken
      const committedByW1 = await repository.recordAttemptAndTransition({
        tenantId,
        notificationId: notifId,
        outboxId: workItem1.outboxId,
        attemptNumber: 1,
        state: "provider_accepted",
        startedAt: clock.now(),
        completedAt: clock.now(),
        fenceToken: workItem1.fenceToken, // Stale fence token!
        nextAction: {
          action: "complete",
          finalNotificationStatus: "sent",
        },
      });

      // Fencing rejected Worker 1's commit!
      expect(committedByW1).toBe(false);

      // Valid Worker 2 commits result with its active fenceToken
      const committedByW2 = await repository.recordAttemptAndTransition({
        tenantId,
        notificationId: notifId,
        outboxId: workItem2.outboxId,
        attemptNumber: 1,
        state: "provider_accepted",
        startedAt: clock.now(),
        completedAt: clock.now(),
        fenceToken: workItem2.fenceToken, // Valid fence token
        nextAction: {
          action: "complete",
          finalNotificationStatus: "sent",
        },
      });

      expect(committedByW2).toBe(true);

      const notif = await repository.getNotification(tenantId, notifId);
      expect(notif?.status).toBe("sent");
    });

    it("in-memory repository skips outbox work if notification tenant does not match event tenant", async () => {
      // Verifies tenant association enforcement in the in-memory repository implementation.
      // Note: This verifies repository logic in unit tests, not live PostgreSQL database isolation.
      const { repository, clock } = setupTestEnvironment();
      const tenantA = crypto.randomUUID();
      const tenantB = crypto.randomUUID();

      const { result } = await acceptSampleNotification(
        repository,
        tenantA,
        "email",
        "bob@example.com",
        "key-tenant-mismatch",
      );

      const notifId = result.notification!.id;
      const outbox = await repository.getOutboxEventByNotificationId(tenantA, notifId);
      expect(outbox).not.toBeNull();

      // Tamper outbox event tenant to simulate a corrupt or mismatched tenant relation
      outbox!.tenant_id = tenantB;

      const claimed = await repository.claimOutboxWork({
        batchSize: 10,
        leaseDurationMs: 30000,
        now: clock.now(),
      });

      expect(claimed).toHaveLength(0);
      const notif = await repository.getNotification(tenantA, notifId);
      expect(notif?.status).toBe("accepted"); // Not transitioned to processing
    });
  });

  describe("PostgresNotificationRepository Worker Transaction SQL Verification", () => {
    function createMockClient() {
      const executedQueries: Array<{ text: string; values?: unknown[] }> = [];

      const mockClient = {
        query: vi.fn(async (textOrConfig: string | { text: string; values?: unknown[] }, values?: unknown[]) => {
          const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;
          const vals = typeof textOrConfig === "string" ? values : textOrConfig.values;
          executedQueries.push({ text: text.trim(), values: vals });

          if (text.includes("SELECT") && text.includes("FOR UPDATE OF o SKIP LOCKED")) {
            return {
              rows: [
                {
                  outbox_id: crypto.randomUUID(),
                  tenant_id: "tenant-pg-test",
                  notification_id: crypto.randomUUID(),
                  recipient_id: "pg@example.com",
                  channel: "email",
                  subject: "Notice",
                  body: "Hello",
                  title: null,
                  attempt_count: 0,
                },
              ],
              rowCount: 1,
            } as unknown as QueryResult;
          }

          return { rows: [], rowCount: 1 } as unknown as QueryResult;
        }),
        release: vi.fn(),
      };

      return { mockClient, executedQueries };
    }

    it("executes claimOutboxWork with FOR UPDATE SKIP LOCKED within transaction", async () => {
      const { mockClient, executedQueries } = createMockClient();
      const mockPool = {
        connect: vi.fn(async () => mockClient as unknown as PoolClient),
        query: vi.fn(async () => ({ rows: [], rowCount: 0 } as unknown as QueryResult)),
      } as unknown as Pool;

      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      const claimed = await repo.claimOutboxWork({
        batchSize: 5,
        leaseDurationMs: 30000,
        now: new Date("2026-10-23T12:00:00Z"),
      });

      expect(claimed).toHaveLength(1);
      expect(executedQueries[0].text).toBe("BEGIN;");
      expect(executedQueries[3].text).toContain("FOR UPDATE OF o SKIP LOCKED;");
      expect(executedQueries[4].text).toContain("UPDATE outbox_events");
      expect(executedQueries[5].text).toContain("UPDATE notifications n");
      expect(executedQueries[5].text).toContain("FROM outbox_events o");
      expect(executedQueries[5].text).toContain("WHERE n.tenant_id = o.tenant_id");
      expect(executedQueries[5].text).toContain("n.id = o.notification_id");
      expect(executedQueries[6].text).toBe("COMMIT;");
      expect(mockClient.release).toHaveBeenCalledTimes(1);
    });

    it("strictly isolates tenant notification records during outbox processing claims", async () => {
      // Regression test: Verifies that the notifications processing update query is explicitly scoped
      // to the tenant associated with each claimed outbox event via safe join (n.tenant_id = o.tenant_id).
      // Note: This unit test verifies SQL structure and parameter binding; true concurrent isolation
      // requires a live PostgreSQL instance with multiple tenant connections.
      const { mockClient, executedQueries } = createMockClient();
      const mockPool = {
        connect: vi.fn(async () => mockClient as unknown as PoolClient),
        query: vi.fn(async () => ({ rows: [], rowCount: 0 } as unknown as QueryResult)),
      } as unknown as Pool;

      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      await repo.claimOutboxWork({
        batchSize: 5,
        leaseDurationMs: 30000,
        now: new Date("2026-10-23T12:00:00Z"),
      });

      const notifUpdate = executedQueries.find((q) =>
        q.text.startsWith("UPDATE notifications"),
      );

      expect(notifUpdate).toBeDefined();
      expect(notifUpdate!.text).toContain("FROM outbox_events o");
      expect(notifUpdate!.text).toContain("WHERE n.tenant_id = o.tenant_id");
      expect(notifUpdate!.text).toContain("n.id = o.notification_id");
      expect(notifUpdate!.text).toContain("o.id = ANY($1::uuid[])");
    });

    it("verifies recordAttemptAndTransition rolls back when lease fencing token does not match", async () => {
      const { mockClient, executedQueries } = createMockClient();

      mockClient.query.mockImplementation(async (textOrConfig: string | { text: string }) => {
        const text = typeof textOrConfig === "string" ? textOrConfig : textOrConfig.text;
        executedQueries.push({ text: text.trim() });

        if (text.includes("UPDATE outbox_events")) {
          // Simulate 0 rows updated due to fenceToken mismatch
          return { rows: [], rowCount: 0 } as unknown as QueryResult;
        }

        return { rows: [], rowCount: 1 } as unknown as QueryResult;
      });

      const mockPool = {
        connect: vi.fn(async () => mockClient as unknown as PoolClient),
        query: vi.fn(async () => ({ rows: [], rowCount: 0 } as unknown as QueryResult)),
      } as unknown as Pool;

      const repo = new PostgresNotificationRepository(mockPool, fingerprintConfig);

      const success = await repo.recordAttemptAndTransition({
        tenantId: "tenant-pg",
        notificationId: crypto.randomUUID(),
        outboxId: crypto.randomUUID(),
        attemptNumber: 1,
        state: "provider_accepted",
        startedAt: new Date(),
        completedAt: new Date(),
        fenceToken: new Date("2026-10-23T11:00:00Z"),
        nextAction: {
          action: "complete",
          finalNotificationStatus: "sent",
        },
      });

      expect(success).toBe(false);
      const rollbackExecuted = executedQueries.some((q) => q.text.includes("ROLLBACK;"));
      expect(rollbackExecuted).toBe(true);
      expect(mockClient.release).toHaveBeenCalledTimes(1);
    });
  });
});
