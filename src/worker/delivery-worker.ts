import type {
  Clock,
  DeliveryWorkerConfig,
  WorkerProcessResult,
} from "./types";
import type {
  ClaimedOutboxWork,
  NotificationRepository,
  RecordAttemptParams,
} from "../notifications";
import type {
  EmailDispatchPayload,
  ProviderAdapter,
  ProviderAttemptResult,
  PushDispatchPayload,
} from "../providers";

export const DEFAULT_BATCH_SIZE = 10;
export const DEFAULT_LEASE_DURATION_MS = 30000; // 30 seconds
export const MAX_DELIVERY_ATTEMPTS = 5;

// Backoff delays in milliseconds for retryable failures:
// Attempt 1 -> 1s, Attempt 2 -> 2s, Attempt 3 -> 4s, Attempt 4 -> 8s (max delay 30s)
export const RETRY_DELAYS_MS = [1000, 2000, 4000, 8000];

export function calculateRetryDelayMs(attemptNumber: number): number {
  const index = attemptNumber - 1;
  const delay = RETRY_DELAYS_MS[index] ?? 8000;
  return Math.min(30000, delay);
}

const defaultClock: Clock = {
  now: () => new Date(),
  sleep: (ms: number) => new Promise((resolve) => setTimeout(resolve, ms)),
};

/**
 * Design Pattern 2: Transactional Outbox Pattern
 *
 * The DeliveryWorker acts as the consumer half of the Transactional Outbox.
 * It polls for durable pending outbox records, transactionally acquires lease locks,
 * and ensures reliable asynchronous dispatch without dual-write synchronization bugs.
 */
export class DeliveryWorker {
  private readonly repository: NotificationRepository;
  private readonly emailAdapter: ProviderAdapter<EmailDispatchPayload>;
  private readonly pushAdapter: ProviderAdapter<PushDispatchPayload>;
  private readonly batchSize: number;
  private readonly leaseDurationMs: number;
  private readonly clock: Clock;
  private isRunning = false;
  private pollTimeout: NodeJS.Timeout | null = null;

  constructor(config: DeliveryWorkerConfig) {
    this.repository = config.repository;
    this.emailAdapter = config.emailAdapter;
    this.pushAdapter = config.pushAdapter;
    this.batchSize = config.batchSize ?? DEFAULT_BATCH_SIZE;
    this.leaseDurationMs =
      config.leaseDurationMs ?? DEFAULT_LEASE_DURATION_MS;
    this.clock = config.clock ?? defaultClock;
  }

  /**
   * Executes a single batch polling and processing run.
   * Used in scheduled cron workers and deterministic test suites.
   */
  async processBatch(): Promise<WorkerProcessResult> {
    const now = this.clock.now();
    const claimedWork = await this.repository.claimOutboxWork({
      batchSize: this.batchSize,
      leaseDurationMs: this.leaseDurationMs,
      now,
    });

    const result: WorkerProcessResult = {
      claimedCount: claimedWork.length,
      processedCount: 0,
      succeededCount: 0,
      retriedCount: 0,
      failedCount: 0,
      suppressedCount: 0,
      timeoutCount: 0,
      fencedOutCount: 0,
    };

    for (const work of claimedWork) {
      const outcome = await this.processSingleWorkItem(work);
      result.processedCount++;

      switch (outcome) {
        case "succeeded":
          result.succeededCount++;
          break;
        case "retried":
          result.retriedCount++;
          break;
        case "failed":
          result.failedCount++;
          break;
        case "suppressed":
          result.suppressedCount++;
          break;
        case "timeout":
          result.timeoutCount++;
          break;
        case "fenced_out":
          result.fencedOutCount++;
          break;
      }
    }

    return result;
  }

  /**
   * Processes a single claimed outbox item.
   *
   * Lifecycle Steps:
   * 1. Pre-send preference check: Re-queries authoritative preferences immediately before provider call.
   * 2. Provider invocation: Executes fake provider call outside of database transactions.
   * 3. State transition & attempt recording: Enforces bounded retries, attempt numbering, and lease fencing.
   */
  private async processSingleWorkItem(
    work: ClaimedOutboxWork,
  ): Promise<
    | "succeeded"
    | "retried"
    | "failed"
    | "suppressed"
    | "timeout"
    | "fenced_out"
  > {
    const {
      outboxId,
      tenantId,
      notificationId,
      recipientId,
      channel,
      currentAttemptCount,
      fenceToken,
    } = work;

    // 1. Pre-send preference check immediately before provider invocation
    const preference = await this.repository.getPreference(
      tenantId,
      recipientId,
      channel,
    );

    if (preference === null || preference === false) {
      const suppressionReason =
        preference === null ? "preference_missing" : "channel_disabled";
      const completedAt = this.clock.now();

      const success = await this.repository.recordAttemptAndTransition({
        tenantId,
        notificationId,
        outboxId,
        attemptNumber: currentAttemptCount,
        state: "started",
        startedAt: completedAt,
        completedAt,
        fenceToken,
        nextAction: {
          action: "suppress",
          suppressionReason,
        },
      });

      return success ? "suppressed" : "fenced_out";
    }

    // 2. Prepare attempt metadata
    const attemptNumber = currentAttemptCount + 1;
    const startedAt = this.clock.now();

    // 3. Design Pattern 3: Strategy Pattern
    // Resolves appropriate channel adapter and executes dispatch outside DB transaction
    let attemptResult: ProviderAttemptResult;
    try {
      if (channel === "email") {
        attemptResult = await this.emailAdapter.send({
          tenantId,
          notificationId,
          recipientId,
          subject: work.subject ?? "",
          body: work.body ?? "",
        });
      } else {
        attemptResult = await this.pushAdapter.send({
          tenantId,
          notificationId,
          recipientId,
          title: work.title ?? "",
          body: work.body ?? "",
        });
      }
    } catch (err) {
      // Uncaught adapter exception treated as transient provider failure
      attemptResult = {
        outcome: "retryable_failure",
        failureCode: "PROVIDER_UNCAUGHT_EXCEPTION",
      };
    }

    const completedAt = this.clock.now();

    // 4. Design Pattern 4: Retry with Exponential Backoff
    // Determines state transition based on attempt outcome and retry budget
    let nextAction: RecordAttemptParams["nextAction"];
    let outcomeLabel:
      | "succeeded"
      | "retried"
      | "failed"
      | "timeout";

    if (attemptResult.outcome === "provider_accepted") {
      nextAction = {
        action: "complete",
        finalNotificationStatus: "sent",
      };
      outcomeLabel = "succeeded";
    } else if (attemptResult.outcome === "timeout_ambiguous") {
      // Never automatically retry an ambiguous timeout; requires review
      nextAction = {
        action: "complete",
        finalNotificationStatus: "delivery_unknown",
      };
      outcomeLabel = "timeout";
    } else if (attemptResult.outcome === "permanent_failure") {
      // Permanent failure immediately terminal
      nextAction = {
        action: "complete",
        finalNotificationStatus: "failed",
      };
      outcomeLabel = "failed";
    } else {
      // attemptResult.outcome === "retryable_failure"
      if (attemptNumber < MAX_DELIVERY_ATTEMPTS) {
        const delayMs = calculateRetryDelayMs(attemptNumber);
        const availableAt = new Date(completedAt.getTime() + delayMs);
        nextAction = {
          action: "reschedule",
          availableAt,
          notificationStatus: "retry_scheduled",
        };
        outcomeLabel = "retried";
      } else {
        // Retry budget exhausted (5 attempts failed)
        nextAction = {
          action: "complete",
          finalNotificationStatus: "failed",
        };
        outcomeLabel = "failed";
      }
    }

    // 5. Commit attempt and state transition under optimistic lease fencing
    const committed = await this.repository.recordAttemptAndTransition({
      tenantId,
      notificationId,
      outboxId,
      attemptNumber,
      state: attemptResult.outcome,
      failureCode: attemptResult.failureCode,
      startedAt,
      completedAt,
      fenceToken,
      nextAction,
    });

    if (!committed) {
      return "fenced_out";
    }

    return outcomeLabel;
  }

  /**
   * Starts background polling loop with specified interval.
   */
  start(intervalMs = 500): void {
    if (this.isRunning) return;
    this.isRunning = true;

    const poll = async () => {
      if (!this.isRunning) return;
      try {
        await this.processBatch();
      } catch {
        // Polling loop swallows error to prevent worker crash
      } finally {
        if (this.isRunning) {
          this.pollTimeout = setTimeout(poll, intervalMs);
        }
      }
    };

    void poll();
  }

  /**
   * Stops background polling loop.
   */
  stop(): void {
    this.isRunning = false;
    if (this.pollTimeout) {
      clearTimeout(this.pollTimeout);
      this.pollTimeout = null;
    }
  }
}
