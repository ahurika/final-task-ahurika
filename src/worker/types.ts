import type {
  ProviderAdapter,
  EmailDispatchPayload,
  PushDispatchPayload,
} from "../providers";
import type { NotificationRepository } from "../notifications";

export interface Clock {
  now(): Date;
  sleep(ms: number): Promise<void>;
}

export interface DeliveryWorkerConfig {
  repository: NotificationRepository;
  emailAdapter: ProviderAdapter<EmailDispatchPayload>;
  pushAdapter: ProviderAdapter<PushDispatchPayload>;
  batchSize?: number;
  leaseDurationMs?: number;
  clock?: Clock;
}

export interface WorkerProcessResult {
  claimedCount: number;
  processedCount: number;
  succeededCount: number;
  retriedCount: number;
  failedCount: number;
  suppressedCount: number;
  timeoutCount: number;
  fencedOutCount: number;
}
