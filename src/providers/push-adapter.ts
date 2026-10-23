import crypto from "node:crypto";
import type {
  FakeProviderMode,
  ProviderAdapter,
  ProviderAttemptResult,
  PushDispatchPayload,
} from "./types";

export interface FakePushAdapterOptions {
  initialMode?: FakeProviderMode;
  sequence?: Array<ProviderAttemptResult>;
}

/**
 * Design Pattern: Adapter Pattern
 *
 * Implements a testable fake push notification provider adapter.
 * Supports configurable deterministic outcomes (success, transient failure, permanent failure,
 * timeout) and programmatic multi-attempt sequences without contacting external APNs or FCM push services.
 */
export class FakePushProviderAdapter
  implements ProviderAdapter<PushDispatchPayload>
{
  readonly channel = "push" as const;
  private mode: FakeProviderMode;
  private sequence: Array<ProviderAttemptResult> = [];
  public readonly sentPayloads: Array<PushDispatchPayload> = [];

  constructor(options: FakePushAdapterOptions = {}) {
    this.mode = options.initialMode ?? "success";
    if (options.sequence) {
      this.sequence = [...options.sequence];
    }
  }

  setMode(mode: FakeProviderMode): void {
    this.mode = mode;
  }

  setSequence(sequence: Array<ProviderAttemptResult>): void {
    this.sequence = [...sequence];
  }

  async send(payload: PushDispatchPayload): Promise<ProviderAttemptResult> {
    this.sentPayloads.push({ ...payload });

    // Check sequence queue first
    if (this.sequence.length > 0) {
      return this.sequence.shift()!;
    }

    switch (this.mode) {
      case "success":
        return {
          outcome: "provider_accepted",
          providerMessageId: `fake-push-${crypto.randomUUID()}`,
        };

      case "transient_failure":
        return {
          outcome: "retryable_failure",
          failureCode: "APNS_OR_FCM_THROTTLED",
        };

      case "permanent_failure":
        return {
          outcome: "permanent_failure",
          failureCode: "INVALID_DEVICE_TOKEN",
        };

      case "timeout":
        return {
          outcome: "timeout_ambiguous",
          failureCode: "PUSH_GATEWAY_TIMEOUT",
        };

      default:
        return {
          outcome: "provider_accepted",
          providerMessageId: `fake-push-${crypto.randomUUID()}`,
        };
    }
  }
}
