import crypto from "node:crypto";
import type { Pool, PoolClient } from "pg";
import type { NotificationChannel } from "../validation";
import { normalizeNotificationContent } from "../idempotency/canonical-serializer";
import {
  resolveFingerprintKey,
  verifyRequestFingerprint,
} from "../idempotency/fingerprint";
import type {
  AcceptNotificationParams,
  AcceptNotificationResult,
  ClaimedOutboxWork,
  ClaimOutboxParams,
  DeliveryAttemptRecord,
  FingerprintKeyConfig,
  NotificationRecord,
  NotificationResponse,
  OutboxEventRecord,
  IdempotencyReservationRecord,
  RecordAttemptParams,
} from "./types";
import { MissingHmacKeyVersionError } from "./types";

/**
 * Design Pattern 1: Repository Pattern
 *
 * Encapsulates all data access, tenant-scoped persistence, transaction boundaries,
 * and concurrency conflict resolution. Callers and API routes interact strictly
 * with typed domain methods rather than issuing raw SQL queries or managing pool connections.
 */
export interface NotificationRepository {
  acceptNotification(
    params: AcceptNotificationParams,
  ): Promise<AcceptNotificationResult>;

  getNotification(
    tenantId: string,
    notificationId: string,
  ): Promise<NotificationRecord | null>;

  getPreference(
    tenantId: string,
    recipientId: string,
    channel: NotificationChannel,
  ): Promise<boolean | null>;

  setPreference(
    tenantId: string,
    recipientId: string,
    channel: NotificationChannel,
    enabled: boolean,
  ): Promise<void>;

  getOutboxEventByNotificationId(
    tenantId: string,
    notificationId: string,
  ): Promise<OutboxEventRecord | null>;

  getReservation(
    tenantId: string,
    idempotencyKey: string,
  ): Promise<IdempotencyReservationRecord | null>;

  claimOutboxWork(params: ClaimOutboxParams): Promise<ClaimedOutboxWork[]>;

  recordAttemptAndTransition(params: RecordAttemptParams): Promise<boolean>;

  getDeliveryAttempts(
    tenantId: string,
    notificationId: string,
  ): Promise<DeliveryAttemptRecord[]>;
}

export const TRANSACTION_RETRY_DELAYS_MS = [100, 250];
export const MAX_TRANSACTION_ATTEMPTS = 3;
export const RESERVATION_RETENTION_DAYS = 90;

/**
 * Classifies PostgreSQL error codes into explicitly retryable transaction failures.
 *
 * Retryable SQLSTATE codes:
 * - 40001: serialization_failure
 * - 40P01: deadlock_detected
 * - 55P03: lock_not_available (lock timeout)
 * - 57014: query_canceled (statement timeout)
 */
export function isRetryableTransactionError(error: unknown): boolean {
  if (typeof error !== "object" || error === null) {
    return false;
  }
  const code = (error as { code?: string }).code;
  return (
    code === "40001" ||
    code === "40P01" ||
    code === "55P03" ||
    code === "57014"
  );
}

/**
 * Checks if a database error is a unique constraint violation on (tenant_id, idempotency_key).
 */
export function isIdempotencyKeyConflict(error: unknown): boolean {
  if (typeof error !== "object" || error === null) {
    return false;
  }
  const pgErr = error as {
    code?: string;
    constraint?: string;
    detail?: string;
    table?: string;
  };
  if (pgErr.code !== "23505") {
    return false;
  }
  return (
    pgErr.constraint ===
      "idempotency_reservations_tenant_id_idempotency_key_key" ||
    pgErr.table === "idempotency_reservations" ||
    Boolean(pgErr.detail?.includes("idempotency_key"))
  );
}

/**
 * Safely parses BYTEA values returned from PostgreSQL.
 * Depending on the pg driver configuration or mock, BYTEA may be returned
 * as a Buffer or as a hex-encoded string (e.g. "\x...").
 */
export function parseByteaBuffer(raw: unknown): Buffer | null {
  if (Buffer.isBuffer(raw)) {
    return raw;
  }
  if (typeof raw === "string") {
    const hex = raw.startsWith("\\x") ? raw.slice(2) : raw;
    return Buffer.from(hex, "hex");
  }
  return null;
}

/**
 * PostgreSQL authoritative implementation of NotificationRepository.
 *
 * Design Pattern 2: Transactional Outbox Pattern
 *
 * Implements atomic notification acceptance and outbox dispatch event creation
 * within a single PostgreSQL transaction. The notification record, idempotency reservation,
 * and dispatch outbox event are durably committed synchronously before acknowledging acceptance,
 * eliminating the dual-write problem between database persistence and queueing.
 */
export class PostgresNotificationRepository implements NotificationRepository {
  private readonly pool: Pool;
  private readonly config: FingerprintKeyConfig;
  private readonly sleep: (ms: number) => Promise<void>;

  constructor(
    pool: Pool,
    config: FingerprintKeyConfig,
    sleepFn: (ms: number) => Promise<void> = (ms) =>
      new Promise((resolve) => setTimeout(resolve, ms)),
  ) {
    this.pool = pool;
    this.config = config;
    this.sleep = sleepFn;
  }

  async acceptNotification(
    params: AcceptNotificationParams,
  ): Promise<AcceptNotificationResult> {
    const { tenantId, idempotencyKey, payload, fingerprintResult } = params;

    for (let attempt = 1; attempt <= MAX_TRANSACTION_ATTEMPTS; attempt++) {
      const client = await this.pool.connect();
      try {
        await client.query("BEGIN;");
        await client.query("SET LOCAL lock_timeout = '5s';");
        await client.query("SET LOCAL statement_timeout = '5s';");

        // 1. Preference check (tenant-scoped)
        const prefResult = await client.query<{ enabled: boolean }>(
          `SELECT enabled
           FROM notification_preferences
           WHERE tenant_id = $1 AND recipient_id = $2 AND channel = $3;`,
          [tenantId, payload.recipient_id, payload.channel],
        );

        const now = new Date();
        const notificationId = crypto.randomUUID();
        const reservationId = crypto.randomUUID();
        const outboxEventId = crypto.randomUUID();

        let isSuppressed = false;
        let suppressionReason: "preference_missing" | "channel_disabled" | null =
          null;
        let status: "accepted" | "suppressed" = "accepted";
        let terminalAt: Date | null = null;

        if (prefResult.rows.length === 0) {
          isSuppressed = true;
          suppressionReason = "preference_missing";
          status = "suppressed";
          terminalAt = now;
        } else if (!prefResult.rows[0].enabled) {
          isSuppressed = true;
          suppressionReason = "channel_disabled";
          status = "suppressed";
          terminalAt = now;
        }

        // 2. Extract and normalize content
        // Design Pattern 3: Strategy Pattern
        // Channel-specific field mapping ensures compliance with notifications_rendered_content_check
        const normalized = normalizeNotificationContent(payload);
        const subject =
          payload.channel === "email" ? normalized.field1.value : null;
        const title =
          payload.channel === "push" ? normalized.field1.value : null;
        const body = normalized.field2.value;

        // 3. Insert notification
        await client.query(
          `INSERT INTO notifications (
            id,
            tenant_id,
            recipient_id,
            channel,
            subject,
            body,
            title,
            status,
            attempt_count,
            suppression_reason,
            accepted_at,
            terminal_at
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12);`,
          [
            notificationId,
            tenantId,
            payload.recipient_id,
            payload.channel,
            subject,
            body,
            title,
            status,
            0,
            suppressionReason,
            now,
            terminalAt,
          ],
        );

        // 4. Insert idempotency reservation (expires 90 days from accepted_at)
        const expiresAt = new Date(
          now.getTime() + RESERVATION_RETENTION_DAYS * 24 * 60 * 60 * 1000,
        );

        await client.query(
          `INSERT INTO idempotency_reservations (
            reservation_id,
            tenant_id,
            idempotency_key,
            notification_id,
            request_fingerprint,
            fingerprint_format_version,
            hmac_key_version,
            accepted_at,
            expires_at,
            created_at
          ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10);`,
          [
            reservationId,
            tenantId,
            idempotencyKey,
            notificationId,
            fingerprintResult.fingerprint,
            fingerprintResult.fingerprintFormatVersion,
            fingerprintResult.hmacKeyVersion,
            now,
            expiresAt,
            now,
          ],
        );

        // 5. Insert outbox event only if eligible (non-suppressed)
        if (!isSuppressed) {
          await client.query(
            `INSERT INTO outbox_events (
              id,
              tenant_id,
              notification_id,
              state,
              available_at,
              created_at,
              updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7);`,
            [outboxEventId, tenantId, notificationId, "pending", now, now, now],
          );
        }

        await client.query("COMMIT;");

        const notificationResponse: NotificationResponse = {
          id: notificationId,
          status,
          channel: payload.channel,
          recipient_id: payload.recipient_id,
          suppression_reason: suppressionReason ?? undefined,
          accepted_at: now.toISOString(),
        };

        return {
          outcome: "created",
          notification: notificationResponse,
        };
      } catch (error) {
        try {
          await client.query("ROLLBACK;");
        } catch {
          // Ignore rollback failure if connection was lost
        }

        // Check for idempotency uniqueness conflict
        if (isIdempotencyKeyConflict(error)) {
          // Transaction lost the concurrency race.
          // Read the committed winner in a fresh statement snapshot.
          const winner = await this.readCommittedWinner(
            client,
            tenantId,
            idempotencyKey,
          );

          if (winner) {
            return this.evaluateWinnerIdempotency(payload, winner);
          }

          // Winner could not be read (e.g. winner rolled back or was cleaned up).
          // Restart acceptance attempt if budget permits.
          if (attempt < MAX_TRANSACTION_ATTEMPTS) {
            const delay = TRANSACTION_RETRY_DELAYS_MS[attempt - 1] ?? 250;
            await this.sleep(delay);
            continue;
          }
        }

        // Check for retryable transaction failures (serialization, deadlock, lock/stmt timeout)
        if (
          isRetryableTransactionError(error) &&
          attempt < MAX_TRANSACTION_ATTEMPTS
        ) {
          const delay = TRANSACTION_RETRY_DELAYS_MS[attempt - 1] ?? 250;
          await this.sleep(delay);
          continue;
        }

        throw error;
      } finally {
        client.release();
      }
    }

    throw new Error("Transaction attempts exhausted.");
  }

  private async readCommittedWinner(
    client: PoolClient,
    tenantId: string,
    idempotencyKey: string,
  ): Promise<{
    notification_id: string;
    status: NotificationRecord["status"];
    channel: NotificationChannel;
    recipient_id: string;
    suppression_reason: NotificationRecord["suppression_reason"];
    accepted_at: Date;
    request_fingerprint: Buffer;
    fingerprint_format_version: number;
    hmac_key_version: number;
  } | null> {
    const result = await client.query<{
      notification_id: string;
      status: NotificationRecord["status"];
      channel: NotificationChannel;
      recipient_id: string;
      suppression_reason: NotificationRecord["suppression_reason"];
      accepted_at: Date;
      request_fingerprint: Buffer;
      fingerprint_format_version: number;
      hmac_key_version: number;
    }>(
      `SELECT
        r.request_fingerprint,
        r.fingerprint_format_version,
        r.hmac_key_version,
        n.id AS notification_id,
        n.status,
        n.channel,
        n.recipient_id,
        n.suppression_reason,
        n.accepted_at
       FROM idempotency_reservations r
       JOIN notifications n
         ON r.tenant_id = n.tenant_id AND r.notification_id = n.id
       WHERE r.tenant_id = $1 AND r.idempotency_key = $2;`,
      [tenantId, idempotencyKey],
    );

    const row = result.rows[0];
    if (!row) {
      return null;
    }

    const fingerprint = parseByteaBuffer(row.request_fingerprint);
    if (!fingerprint) {
      return null;
    }

    return {
      notification_id: row.notification_id,
      status: row.status,
      channel: row.channel,
      recipient_id: row.recipient_id,
      suppression_reason: row.suppression_reason,
      accepted_at: row.accepted_at,
      request_fingerprint: fingerprint,
      fingerprint_format_version: row.fingerprint_format_version,
      hmac_key_version: row.hmac_key_version,
    };
  }

  private evaluateWinnerIdempotency(
    payload: AcceptNotificationParams["payload"],
    winner: {
      notification_id: string;
      status: NotificationRecord["status"];
      channel: NotificationChannel;
      recipient_id: string;
      suppression_reason: NotificationRecord["suppression_reason"];
      accepted_at: Date;
      request_fingerprint: Buffer;
      fingerprint_format_version: number;
      hmac_key_version: number;
    },
  ): AcceptNotificationResult {
    const historicalHmacKey = resolveFingerprintKey(
      winner.hmac_key_version,
      this.config.hmacKey,
      this.config.activeKeyVersion,
      this.config.previousKeys,
    );

    if (!historicalHmacKey) {
      // Critical security rule: NEVER fall back silently to the active HMAC key.
      // Must fail closed with internal configuration error.
      throw new MissingHmacKeyVersionError(winner.hmac_key_version);
    }

    const isMatch = verifyRequestFingerprint(
      payload,
      winner.request_fingerprint,
      historicalHmacKey,
    );

    if (isMatch) {
      return {
        outcome: "replayed",
        notification: {
          id: winner.notification_id,
          status: winner.status,
          channel: winner.channel,
          recipient_id: winner.recipient_id,
          suppression_reason: winner.suppression_reason ?? undefined,
          accepted_at: new Date(winner.accepted_at).toISOString(),
        },
      };
    }

    return {
      outcome: "conflict",
    };
  }

  async getNotification(
    tenantId: string,
    notificationId: string,
  ): Promise<NotificationRecord | null> {
    const res = await this.pool.query<NotificationRecord>(
      `SELECT
        id,
        tenant_id,
        recipient_id,
        channel,
        subject,
        body,
        title,
        status,
        attempt_count,
        suppression_reason,
        accepted_at,
        terminal_at
       FROM notifications
       WHERE tenant_id = $1 AND id = $2;`,
      [tenantId, notificationId],
    );
    return res.rows[0] ?? null;
  }

  async getPreference(
    tenantId: string,
    recipientId: string,
    channel: NotificationChannel,
  ): Promise<boolean | null> {
    const res = await this.pool.query<{ enabled: boolean }>(
      `SELECT enabled
       FROM notification_preferences
       WHERE tenant_id = $1 AND recipient_id = $2 AND channel = $3;`,
      [tenantId, recipientId, channel],
    );
    if (res.rows.length === 0) {
      return null;
    }
    return res.rows[0].enabled;
  }

  async setPreference(
    tenantId: string,
    recipientId: string,
    channel: NotificationChannel,
    enabled: boolean,
  ): Promise<void> {
    await this.pool.query(
      `INSERT INTO notification_preferences (
        tenant_id,
        recipient_id,
        channel,
        enabled,
        updated_at
      ) VALUES ($1, $2, $3, $4, NOW())
      ON CONFLICT (tenant_id, recipient_id, channel)
      DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = NOW();`,
      [tenantId, recipientId, channel, enabled],
    );
  }

  async getOutboxEventByNotificationId(
    tenantId: string,
    notificationId: string,
  ): Promise<OutboxEventRecord | null> {
    const res = await this.pool.query<OutboxEventRecord>(
      `SELECT
        id,
        tenant_id,
        notification_id,
        state,
        available_at,
        lease_expires_at,
        created_at,
        updated_at,
        completed_at
       FROM outbox_events
       WHERE tenant_id = $1 AND notification_id = $2;`,
      [tenantId, notificationId],
    );
    return res.rows[0] ?? null;
  }

  async getReservation(
    tenantId: string,
    idempotencyKey: string,
  ): Promise<IdempotencyReservationRecord | null> {
    const res = await this.pool.query<IdempotencyReservationRecord>(
      `SELECT
        reservation_id,
        tenant_id,
        idempotency_key,
        notification_id,
        request_fingerprint,
        fingerprint_format_version,
        hmac_key_version,
        accepted_at,
        expires_at,
        created_at
       FROM idempotency_reservations
       WHERE tenant_id = $1 AND idempotency_key = $2;`,
      [tenantId, idempotencyKey],
    );
    const row = res.rows[0];
    if (!row) {
      return null;
    }

    const fingerprint = parseByteaBuffer(row.request_fingerprint);
    if (!fingerprint) {
      return null;
    }

    return {
      ...row,
      request_fingerprint: fingerprint,
    };
  }

  /**
   * Transactionally claims eligible outbox rows using FOR UPDATE SKIP LOCKED.
   * Updates state to 'claimed', advances lease_expires_at, and marks notifications 'processing'.
   * Returns claimed work with fenceToken (lease_expires_at) for stale worker protection.
   */
  async claimOutboxWork(
    params: ClaimOutboxParams,
  ): Promise<ClaimedOutboxWork[]> {
    const { batchSize, leaseDurationMs } = params;
    const now = params.now ?? new Date();
    const leaseExpiresAt = new Date(now.getTime() + leaseDurationMs);

    const client = await this.pool.connect();
    try {
      await client.query("BEGIN;");
      await client.query("SET LOCAL lock_timeout = '5s';");
      await client.query("SET LOCAL statement_timeout = '5s';");

      const selectRes = await client.query<{
        outbox_id: string;
        tenant_id: string;
        notification_id: string;
        recipient_id: string;
        channel: NotificationChannel;
        subject: string | null;
        body: string | null;
        title: string | null;
        attempt_count: number;
      }>(
        `SELECT
           o.id AS outbox_id,
           o.tenant_id,
           o.notification_id,
           n.recipient_id,
           n.channel,
           n.subject,
           n.body,
           n.title,
           n.attempt_count
         FROM outbox_events o
         JOIN notifications n
           ON o.tenant_id = n.tenant_id AND o.notification_id = n.id
         WHERE (o.state = 'pending' AND o.available_at <= $1)
            OR (o.state = 'claimed' AND o.lease_expires_at <= $1)
         ORDER BY o.available_at ASC
         LIMIT $2
         FOR UPDATE OF o SKIP LOCKED;`,
        [now, batchSize],
      );

      if (selectRes.rows.length === 0) {
        await client.query("COMMIT;");
        return [];
      }

      const outboxIds = selectRes.rows.map((r) => r.outbox_id);

      await client.query(
        `UPDATE outbox_events
         SET state = 'claimed',
             lease_expires_at = $1,
             updated_at = $2
         WHERE id = ANY($3::uuid[]);`,
        [leaseExpiresAt, now, outboxIds],
      );

      // Explicitly scoped to the tenant associated with each claimed outbox event via safe join
      await client.query(
        `UPDATE notifications n
         SET status = 'processing'
         FROM outbox_events o
         WHERE n.tenant_id = o.tenant_id
           AND n.id = o.notification_id
           AND o.id = ANY($1::uuid[])
           AND n.status IN ('accepted', 'queued', 'retry_scheduled');`,
        [outboxIds],
      );

      await client.query("COMMIT;");

      return selectRes.rows.map((r) => ({
        outboxId: r.outbox_id,
        tenantId: r.tenant_id,
        notificationId: r.notification_id,
        recipientId: r.recipient_id,
        channel: r.channel,
        subject: r.subject,
        body: r.body,
        title: r.title,
        currentAttemptCount: r.attempt_count,
        fenceToken: leaseExpiresAt,
      }));
    } catch (err) {
      try {
        await client.query("ROLLBACK;");
      } catch {
        // ignore rollback error
      }
      throw err;
    } finally {
      client.release();
    }
  }

  /**
   * Atomically records delivery attempt and advances notification and outbox states.
   * Enforces optimistic lease fencing: only commits if outbox_events.lease_expires_at matches fenceToken.
   * If the lease expired and another worker reclaimed the event, returns false without committing.
   */
  async recordAttemptAndTransition(
    params: RecordAttemptParams,
  ): Promise<boolean> {
    const {
      tenantId,
      notificationId,
      outboxId,
      attemptNumber,
      state,
      failureCode,
      startedAt,
      completedAt,
      fenceToken,
      nextAction,
    } = params;

    const attemptId = crypto.randomUUID();
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN;");
      await client.query("SET LOCAL lock_timeout = '5s';");
      await client.query("SET LOCAL statement_timeout = '5s';");

      let outboxUpdateSql = "";
      let outboxParams: unknown[] = [];

      if (nextAction.action === "complete" || nextAction.action === "suppress") {
        outboxUpdateSql = `
          UPDATE outbox_events
          SET state = 'completed',
              completed_at = $1,
              updated_at = $1
          WHERE id = $2 AND state = 'claimed' AND lease_expires_at = $3;
        `;
        outboxParams = [completedAt, outboxId, fenceToken];
      } else {
        outboxUpdateSql = `
          UPDATE outbox_events
          SET state = 'pending',
              available_at = $1,
              lease_expires_at = NULL,
              updated_at = $2
          WHERE id = $3 AND state = 'claimed' AND lease_expires_at = $4;
        `;
        outboxParams = [
          nextAction.availableAt,
          completedAt,
          outboxId,
          fenceToken,
        ];
      }

      const outboxRes = await client.query(outboxUpdateSql, outboxParams);
      if (outboxRes.rowCount === 0) {
        // Fencing violation: lease expired and was reclaimed by another worker
        await client.query("ROLLBACK;");
        return false;
      }

      if (nextAction.action !== "suppress") {
        await client.query(
          `INSERT INTO delivery_attempts (
             id,
             tenant_id,
             notification_id,
             attempt_number,
             state,
             started_at,
             completed_at,
             failure_code
           ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8);`,
          [
            attemptId,
            tenantId,
            notificationId,
            attemptNumber,
            state,
            startedAt,
            completedAt,
            failureCode ?? null,
          ],
        );
      }

      if (nextAction.action === "complete") {
        await client.query(
          `UPDATE notifications
           SET status = $1,
               attempt_count = $2,
               terminal_at = $3
           WHERE tenant_id = $4 AND id = $5;`,
          [
            nextAction.finalNotificationStatus,
            attemptNumber,
            completedAt,
            tenantId,
            notificationId,
          ],
        );
      } else if (nextAction.action === "reschedule") {
        await client.query(
          `UPDATE notifications
           SET status = 'retry_scheduled',
               attempt_count = $1,
               terminal_at = NULL
           WHERE tenant_id = $2 AND id = $3;`,
          [attemptNumber, tenantId, notificationId],
        );
      } else if (nextAction.action === "suppress") {
        await client.query(
          `UPDATE notifications
           SET status = 'suppressed',
               suppression_reason = $1,
               terminal_at = $2
           WHERE tenant_id = $3 AND id = $4;`,
          [nextAction.suppressionReason, completedAt, tenantId, notificationId],
        );
      }

      await client.query("COMMIT;");
      return true;
    } catch (err) {
      try {
        await client.query("ROLLBACK;");
      } catch {
        // ignore rollback error
      }
      throw err;
    } finally {
      client.release();
    }
  }

  /**
   * Retrieves append-only attempt history for a given notification.
   */
  async getDeliveryAttempts(
    tenantId: string,
    notificationId: string,
  ): Promise<DeliveryAttemptRecord[]> {
    const res = await this.pool.query<DeliveryAttemptRecord>(
      `SELECT
         id,
         tenant_id,
         notification_id,
         attempt_number,
         state,
         started_at,
         completed_at,
         failure_code
       FROM delivery_attempts
       WHERE tenant_id = $1 AND notification_id = $2
       ORDER BY attempt_number ASC;`,
      [tenantId, notificationId],
    );
    return res.rows;
  }
}

/**
 * Thread-safe, asynchronous in-memory implementation of NotificationRepository.
 *
 * Used for isolated, deterministic, high-speed unit and integration tests
 * without requiring an active external database daemon or running migrations.
 */
export class InMemoryNotificationRepository implements NotificationRepository {
  private readonly notifications = new Map<string, NotificationRecord>();
  private readonly preferences = new Map<string, boolean>();
  private readonly reservations = new Map<
    string,
    IdempotencyReservationRecord
  >();
  private readonly outboxEvents = new Map<string, OutboxEventRecord>();
  private readonly deliveryAttempts = new Map<
    string,
    DeliveryAttemptRecord[]
  >();
  private readonly config: FingerprintKeyConfig;

  // Hooks for failure injection in tests
  public simulatedFailures: Array<Error> = [];

  constructor(config: FingerprintKeyConfig) {
    this.config = config;
  }

  private preferenceKey(
    tenantId: string,
    recipientId: string,
    channel: string,
  ): string {
    return `${tenantId}::${recipientId}::${channel}`;
  }

  private reservationKey(tenantId: string, idempotencyKey: string): string {
    return `${tenantId}::${idempotencyKey}`;
  }

  async acceptNotification(
    params: AcceptNotificationParams,
  ): Promise<AcceptNotificationResult> {
    const { tenantId, idempotencyKey, payload, fingerprintResult } = params;

    if (this.simulatedFailures.length > 0) {
      const error = this.simulatedFailures.shift()!;
      throw error;
    }

    const resKey = this.reservationKey(tenantId, idempotencyKey);
    const existingReservation = this.reservations.get(resKey);

    if (existingReservation) {
      // Existing reservation found: resolve historical key and verify
      const historicalKey = resolveFingerprintKey(
        existingReservation.hmac_key_version,
        this.config.hmacKey,
        this.config.activeKeyVersion,
        this.config.previousKeys,
      );

      if (!historicalKey) {
        throw new MissingHmacKeyVersionError(
          existingReservation.hmac_key_version,
        );
      }

      const isMatch = verifyRequestFingerprint(
        payload,
        existingReservation.request_fingerprint,
        historicalKey,
      );

      if (!isMatch) {
        return { outcome: "conflict" };
      }

      const notification = this.notifications.get(
        existingReservation.notification_id,
      );
      if (!notification) {
        throw new Error("Data corruption: referenced notification not found.");
      }

      return {
        outcome: "replayed",
        notification: {
          id: notification.id,
          status: notification.status,
          channel: notification.channel,
          recipient_id: notification.recipient_id,
          suppression_reason: notification.suppression_reason ?? undefined,
          accepted_at: notification.accepted_at.toISOString(),
        },
      };
    }

    // Check preference
    const prefKey = this.preferenceKey(
      tenantId,
      payload.recipient_id,
      payload.channel,
    );
    const prefEnabled = this.preferences.get(prefKey);

    const now = new Date();
    const notificationId = crypto.randomUUID();
    const reservationId = crypto.randomUUID();
    const outboxId = crypto.randomUUID();

    let isSuppressed = false;
    let suppressionReason: "preference_missing" | "channel_disabled" | null =
      null;
    let status: "accepted" | "suppressed" = "accepted";
    let terminalAt: Date | null = null;

    if (prefEnabled === undefined) {
      isSuppressed = true;
      suppressionReason = "preference_missing";
      status = "suppressed";
      terminalAt = now;
    } else if (!prefEnabled) {
      isSuppressed = true;
      suppressionReason = "channel_disabled";
      status = "suppressed";
      terminalAt = now;
    }

    const normalized = normalizeNotificationContent(payload);
    const subject =
      payload.channel === "email" ? normalized.field1.value : null;
    const title = payload.channel === "push" ? normalized.field1.value : null;
    const body = normalized.field2.value;

    const notificationRecord: NotificationRecord = {
      id: notificationId,
      tenant_id: tenantId,
      recipient_id: payload.recipient_id,
      channel: payload.channel,
      subject,
      body,
      title,
      status,
      attempt_count: 0,
      suppression_reason: suppressionReason,
      accepted_at: now,
      terminal_at: terminalAt,
    };

    const reservationRecord: IdempotencyReservationRecord = {
      reservation_id: reservationId,
      tenant_id: tenantId,
      idempotency_key: idempotencyKey,
      notification_id: notificationId,
      request_fingerprint: fingerprintResult.fingerprint,
      fingerprint_format_version: fingerprintResult.fingerprintFormatVersion,
      hmac_key_version: fingerprintResult.hmacKeyVersion,
      accepted_at: now,
      expires_at: new Date(
        now.getTime() + RESERVATION_RETENTION_DAYS * 24 * 60 * 60 * 1000,
      ),
      created_at: now,
    };

    // Atomic simulation: write all candidate records
    this.notifications.set(notificationId, notificationRecord);
    this.reservations.set(resKey, reservationRecord);

    if (!isSuppressed) {
      this.outboxEvents.set(notificationId, {
        id: outboxId,
        tenant_id: tenantId,
        notification_id: notificationId,
        state: "pending",
        available_at: now,
        lease_expires_at: null,
        created_at: now,
        updated_at: now,
        completed_at: null,
      });
    }

    return {
      outcome: "created",
      notification: {
        id: notificationId,
        status,
        channel: payload.channel,
        recipient_id: payload.recipient_id,
        suppression_reason: suppressionReason ?? undefined,
        accepted_at: now.toISOString(),
      },
    };
  }

  async getNotification(
    tenantId: string,
    notificationId: string,
  ): Promise<NotificationRecord | null> {
    const record = this.notifications.get(notificationId);
    if (!record || record.tenant_id !== tenantId) {
      return null;
    }
    return record;
  }

  async getPreference(
    tenantId: string,
    recipientId: string,
    channel: NotificationChannel,
  ): Promise<boolean | null> {
    const key = this.preferenceKey(tenantId, recipientId, channel);
    const pref = this.preferences.get(key);
    return pref ?? null;
  }

  async setPreference(
    tenantId: string,
    recipientId: string,
    channel: NotificationChannel,
    enabled: boolean,
  ): Promise<void> {
    const key = this.preferenceKey(tenantId, recipientId, channel);
    this.preferences.set(key, enabled);
  }

  async getOutboxEventByNotificationId(
    tenantId: string,
    notificationId: string,
  ): Promise<OutboxEventRecord | null> {
    const record = this.outboxEvents.get(notificationId);
    if (!record || record.tenant_id !== tenantId) {
      return null;
    }
    return record;
  }

  async getReservation(
    tenantId: string,
    idempotencyKey: string,
  ): Promise<IdempotencyReservationRecord | null> {
    const key = this.reservationKey(tenantId, idempotencyKey);
    const record = this.reservations.get(key);
    return record ?? null;
  }

  async claimOutboxWork(
    params: ClaimOutboxParams,
  ): Promise<ClaimedOutboxWork[]> {
    const { batchSize, leaseDurationMs } = params;
    const now = params.now ?? new Date();
    const leaseExpiresAt = new Date(now.getTime() + leaseDurationMs);

    const eligibleEvents = Array.from(this.outboxEvents.values())
      .filter((event) => {
        if (event.state === "pending" && event.available_at <= now) {
          return true;
        }
        if (
          event.state === "claimed" &&
          event.lease_expires_at !== null &&
          event.lease_expires_at <= now
        ) {
          return true;
        }
        return false;
      })
      .sort((a, b) => a.available_at.getTime() - b.available_at.getTime())
      .slice(0, batchSize);

    const result: ClaimedOutboxWork[] = [];

    for (const event of eligibleEvents) {
      const notif = this.notifications.get(event.notification_id);
      if (!notif || notif.tenant_id !== event.tenant_id) {
        continue;
      }

      event.state = "claimed";
      event.lease_expires_at = leaseExpiresAt;
      event.updated_at = now;

      if (
        notif.status === "accepted" ||
        notif.status === "queued" ||
        notif.status === "retry_scheduled"
      ) {
        notif.status = "processing";
      }

      result.push({
        outboxId: event.id,
        tenantId: event.tenant_id,
        notificationId: event.notification_id,
        recipientId: notif.recipient_id,
        channel: notif.channel,
        subject: notif.subject,
        body: notif.body,
        title: notif.title,
        currentAttemptCount: notif.attempt_count,
        fenceToken: leaseExpiresAt,
      });
    }

    return result;
  }

  async recordAttemptAndTransition(
    params: RecordAttemptParams,
  ): Promise<boolean> {
    const {
      tenantId,
      notificationId,
      outboxId,
      attemptNumber,
      state,
      failureCode,
      startedAt,
      completedAt,
      fenceToken,
      nextAction,
    } = params;

    const event = this.outboxEvents.get(notificationId);
    if (!event || event.id !== outboxId) {
      return false;
    }

    // Fencing check: lease must be active and match the fenceToken
    if (
      event.state !== "claimed" ||
      !event.lease_expires_at ||
      event.lease_expires_at.getTime() !== fenceToken.getTime()
    ) {
      return false;
    }

    const notif = this.notifications.get(notificationId);
    if (!notif || notif.tenant_id !== tenantId) {
      return false;
    }

    if (nextAction.action === "complete") {
      event.state = "completed";
      event.completed_at = completedAt;
      event.updated_at = completedAt;

      notif.status = nextAction.finalNotificationStatus;
      notif.attempt_count = attemptNumber;
      notif.terminal_at = completedAt;
    } else if (nextAction.action === "reschedule") {
      event.state = "pending";
      event.available_at = nextAction.availableAt;
      event.lease_expires_at = null;
      event.updated_at = completedAt;

      notif.status = "retry_scheduled";
      notif.attempt_count = attemptNumber;
      notif.terminal_at = null;
    } else if (nextAction.action === "suppress") {
      event.state = "completed";
      event.completed_at = completedAt;
      event.updated_at = completedAt;

      notif.status = "suppressed";
      notif.suppression_reason = nextAction.suppressionReason;
      notif.terminal_at = completedAt;
    }

    if (nextAction.action !== "suppress") {
      const attempt: DeliveryAttemptRecord = {
        id: crypto.randomUUID(),
        tenant_id: tenantId,
        notification_id: notificationId,
        attempt_number: attemptNumber,
        state,
        started_at: startedAt,
        completed_at: completedAt,
        failure_code: failureCode ?? null,
      };

      const attempts = this.deliveryAttempts.get(notificationId) ?? [];
      attempts.push(attempt);
      this.deliveryAttempts.set(notificationId, attempts);
    }

    return true;
  }

  async getDeliveryAttempts(
    tenantId: string,
    notificationId: string,
  ): Promise<DeliveryAttemptRecord[]> {
    const notif = this.notifications.get(notificationId);
    if (!notif || notif.tenant_id !== tenantId) {
      return [];
    }
    return this.deliveryAttempts.get(notificationId) ?? [];
  }
}
