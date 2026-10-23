import type { NotificationPayload, NotificationChannel } from "../validation";
import type { RequestFingerprintResult } from "../idempotency";

export type NotificationStatus =
  | "accepted"
  | "queued"
  | "processing"
  | "retry_scheduled"
  | "sent"
  | "suppressed"
  | "failed"
  | "delivery_unknown";

export type SuppressionReason = "preference_missing" | "channel_disabled";

export type OutboxState = "pending" | "claimed" | "completed";

export interface NotificationRecord {
  id: string;
  tenant_id: string;
  recipient_id: string;
  channel: NotificationChannel;
  subject: string | null;
  body: string | null;
  title: string | null;
  status: NotificationStatus;
  attempt_count: number;
  suppression_reason: SuppressionReason | null;
  accepted_at: Date;
  terminal_at: Date | null;
}

export interface OutboxEventRecord {
  id: string;
  tenant_id: string;
  notification_id: string;
  state: OutboxState;
  available_at: Date;
  lease_expires_at: Date | null;
  created_at: Date;
  updated_at: Date;
  completed_at: Date | null;
}

export interface IdempotencyReservationRecord {
  reservation_id: string;
  tenant_id: string;
  idempotency_key: string;
  notification_id: string;
  request_fingerprint: Buffer;
  fingerprint_format_version: number;
  hmac_key_version: number;
  accepted_at: Date;
  expires_at: Date;
  created_at: Date;
}

export interface AcceptNotificationParams {
  tenantId: string;
  idempotencyKey: string;
  payload: NotificationPayload;
  fingerprintResult: RequestFingerprintResult;
}

export interface NotificationResponse {
  id: string;
  status: NotificationStatus;
  channel: NotificationChannel;
  recipient_id: string;
  suppression_reason?: SuppressionReason;
  accepted_at: string;
}

export type AcceptNotificationOutcome = "created" | "replayed" | "conflict";

export interface AcceptNotificationResult {
  outcome: AcceptNotificationOutcome;
  notification?: NotificationResponse;
}

export interface FingerprintKeyConfig {
  hmacKey: Buffer;
  activeKeyVersion: number;
  previousKeys: ReadonlyMap<number, Buffer>;
}

export class MissingHmacKeyVersionError extends Error {
  public readonly keyVersion: number;

  constructor(keyVersion: number) {
    super(
      `HMAC key version ${keyVersion} is required to verify historical reservation but is not configured.`,
    );
    this.name = "MissingHmacKeyVersionError";
    this.keyVersion = keyVersion;
  }
}

export type DeliveryAttemptState =
  | "started"
  | "provider_accepted"
  | "retryable_failure"
  | "permanent_failure"
  | "timeout_ambiguous";

export interface DeliveryAttemptRecord {
  id: string;
  tenant_id: string;
  notification_id: string;
  attempt_number: number;
  state: DeliveryAttemptState;
  started_at: Date;
  completed_at: Date | null;
  failure_code: string | null;
}

export interface ClaimedOutboxWork {
  outboxId: string;
  tenantId: string;
  notificationId: string;
  recipientId: string;
  channel: NotificationChannel;
  subject: string | null;
  body: string | null;
  title: string | null;
  currentAttemptCount: number;
  fenceToken: Date; // lease_expires_at used as fencing token against stale workers
}

export interface ClaimOutboxParams {
  batchSize: number;
  leaseDurationMs: number;
  now?: Date;
}

export interface RecordAttemptParams {
  tenantId: string;
  notificationId: string;
  outboxId: string;
  attemptNumber: number;
  state: DeliveryAttemptState;
  failureCode?: string;
  startedAt: Date;
  completedAt: Date;
  fenceToken: Date;
  nextAction:
    | {
        action: "complete";
        finalNotificationStatus: "sent" | "failed" | "delivery_unknown";
      }
    | {
        action: "reschedule";
        availableAt: Date;
        notificationStatus: "retry_scheduled";
      }
    | {
        action: "suppress";
        suppressionReason: SuppressionReason;
      };
}

export class StaleWorkerLeaseError extends Error {
  public readonly outboxId: string;

  constructor(outboxId: string) {
    super(
      `Outbox event ${outboxId} lease expired or was reclaimed by another worker.`,
    );
    this.name = "StaleWorkerLeaseError";
    this.outboxId = outboxId;
  }
}

