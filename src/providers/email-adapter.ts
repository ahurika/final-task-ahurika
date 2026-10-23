import crypto from "node:crypto";
import type {
  EmailDispatchPayload,
  FakeProviderMode,
  ProviderAdapter,
  ProviderAttemptResult,
} from "./types";

export interface FakeEmailAdapterOptions {
  initialMode?: FakeProviderMode;
  sequence?: Array<ProviderAttemptResult>;
}

/**
 * Design Pattern: Adapter Pattern
 *
 * Implements a testable fake email provider adapter.
 * Supports configurable deterministic outcomes (success, transient failure, permanent failure,
 * timeout) and programmatic multi-attempt sequences without relying on flaky real-world network services.
 */
export class FakeEmailProviderAdapter
  implements ProviderAdapter<EmailDispatchPayload>
{
  readonly channel = "email" as const;
  private mode: FakeProviderMode;
  private sequence: Array<ProviderAttemptResult> = [];
  public readonly sentPayloads: Array<EmailDispatchPayload> = [];

  constructor(options: FakeEmailAdapterOptions = {}) {
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

  async send(payload: EmailDispatchPayload): Promise<ProviderAttemptResult> {
    this.sentPayloads.push({ ...payload });

    // Check sequence queue first
    if (this.sequence.length > 0) {
      return this.sequence.shift()!;
    }

    switch (this.mode) {
      case "success":
        return {
          outcome: "provider_accepted",
          providerMessageId: `fake-email-${crypto.randomUUID()}`,
        };

      case "transient_failure":
        return {
          outcome: "retryable_failure",
          failureCode: "PROVIDER_503_UNAVAILABLE",
        };

      case "permanent_failure":
        return {
          outcome: "permanent_failure",
          failureCode: "INVALID_EMAIL_ADDRESS",
        };

      case "timeout":
        return {
          outcome: "timeout_ambiguous",
          failureCode: "PROVIDER_GATEWAY_TIMEOUT",
        };

      default:
        return {
          outcome: "provider_accepted",
          providerMessageId: `fake-email-${crypto.randomUUID()}`,
        };
    }
  }
}
