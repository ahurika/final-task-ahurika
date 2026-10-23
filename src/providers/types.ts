export type ProviderOutcomeState =
  | "provider_accepted"
  | "retryable_failure"
  | "permanent_failure"
  | "timeout_ambiguous";

export interface ProviderAttemptResult {
  outcome: ProviderOutcomeState;
  failureCode?: string;
  providerMessageId?: string;
}

export interface EmailDispatchPayload {
  tenantId: string;
  notificationId: string;
  recipientId: string;
  subject: string;
  body: string;
}

export interface PushDispatchPayload {
  tenantId: string;
  notificationId: string;
  recipientId: string;
  title: string;
  body: string;
}

export type FakeProviderMode =
  | "success"
  | "transient_failure"
  | "permanent_failure"
  | "timeout"
  | "custom";

/**
 * Design Pattern: Adapter Pattern
 *
 * Defines a uniform delivery adapter interface for external channel providers.
 * Decouples the asynchronous delivery worker from channel-specific protocols,
 * payloads, and vendor error formats.
 */
export interface ProviderAdapter<T> {
  readonly channel: "email" | "push";
  send(payload: T): Promise<ProviderAttemptResult>;
}
