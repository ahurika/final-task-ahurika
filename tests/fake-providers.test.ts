import { describe, expect, it } from "vitest";
import {
  FakeEmailProviderAdapter,
  FakePushProviderAdapter,
} from "../src/providers";

describe("Phase 3 — Fake Provider Adapters (FR-03, NFR-08)", () => {
  describe("FakeEmailProviderAdapter", () => {
    it("reports 'email' channel and default 'provider_accepted' on success mode", async () => {
      const adapter = new FakeEmailProviderAdapter({ initialMode: "success" });
      expect(adapter.channel).toBe("email");

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "notif-1",
        recipientId: "alice@example.com",
        subject: "Welcome",
        body: "Hello Alice",
      });

      expect(result.outcome).toBe("provider_accepted");
      expect(result.providerMessageId).toBeDefined();
      expect(result.failureCode).toBeUndefined();
      expect(adapter.sentPayloads).toHaveLength(1);
      expect(adapter.sentPayloads[0].recipientId).toBe("alice@example.com");
    });

    it("returns 'retryable_failure' with PROVIDER_503_UNAVAILABLE on transient failure mode", async () => {
      const adapter = new FakeEmailProviderAdapter({
        initialMode: "transient_failure",
      });

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "notif-2",
        recipientId: "bob@example.com",
        subject: "Notice",
        body: "Hello Bob",
      });

      expect(result.outcome).toBe("retryable_failure");
      expect(result.failureCode).toBe("PROVIDER_503_UNAVAILABLE");
    });

    it("returns 'permanent_failure' with INVALID_EMAIL_ADDRESS on permanent failure mode", async () => {
      const adapter = new FakeEmailProviderAdapter({
        initialMode: "permanent_failure",
      });

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "notif-3",
        recipientId: "invalid-email",
        subject: "Notice",
        body: "Hello",
      });

      expect(result.outcome).toBe("permanent_failure");
      expect(result.failureCode).toBe("INVALID_EMAIL_ADDRESS");
    });

    it("returns 'timeout_ambiguous' with PROVIDER_GATEWAY_TIMEOUT on timeout mode", async () => {
      const adapter = new FakeEmailProviderAdapter({ initialMode: "timeout" });

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "notif-4",
        recipientId: "carol@example.com",
        subject: "Notice",
        body: "Hello Carol",
      });

      expect(result.outcome).toBe("timeout_ambiguous");
      expect(result.failureCode).toBe("PROVIDER_GATEWAY_TIMEOUT");
    });

    it("executes configured sequential multi-attempt outcomes deterministically", async () => {
      const adapter = new FakeEmailProviderAdapter({
        sequence: [
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
            providerMessageId: "success-msg-3",
          },
        ],
      });

      const payload = {
        tenantId: "tenant-1",
        notificationId: "notif-seq",
        recipientId: "seq@example.com",
        subject: "Test",
        body: "Sequence",
      };

      const res1 = await adapter.send(payload);
      expect(res1.outcome).toBe("retryable_failure");

      const res2 = await adapter.send(payload);
      expect(res2.outcome).toBe("retryable_failure");

      const res3 = await adapter.send(payload);
      expect(res3.outcome).toBe("provider_accepted");
      expect(res3.providerMessageId).toBe("success-msg-3");

      expect(adapter.sentPayloads).toHaveLength(3);
    });
  });

  describe("FakePushProviderAdapter", () => {
    it("reports 'push' channel and default 'provider_accepted' on success mode", async () => {
      const adapter = new FakePushProviderAdapter({ initialMode: "success" });
      expect(adapter.channel).toBe("push");

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "push-1",
        recipientId: "device_token_abc123",
        title: "Alert",
        body: "New message received",
      });

      expect(result.outcome).toBe("provider_accepted");
      expect(result.providerMessageId).toBeDefined();
      expect(adapter.sentPayloads).toHaveLength(1);
      expect(adapter.sentPayloads[0].recipientId).toBe("device_token_abc123");
    });

    it("returns 'retryable_failure' with APNS_OR_FCM_THROTTLED on transient failure mode", async () => {
      const adapter = new FakePushProviderAdapter({
        initialMode: "transient_failure",
      });

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "push-2",
        recipientId: "device_token_abc123",
        title: "Alert",
        body: "Throttled message",
      });

      expect(result.outcome).toBe("retryable_failure");
      expect(result.failureCode).toBe("APNS_OR_FCM_THROTTLED");
    });

    it("returns 'permanent_failure' with INVALID_DEVICE_TOKEN on permanent failure mode", async () => {
      const adapter = new FakePushProviderAdapter({
        initialMode: "permanent_failure",
      });

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "push-3",
        recipientId: "bad_device_token",
        title: "Alert",
        body: "Expired device token",
      });

      expect(result.outcome).toBe("permanent_failure");
      expect(result.failureCode).toBe("INVALID_DEVICE_TOKEN");
    });

    it("returns 'timeout_ambiguous' with PUSH_GATEWAY_TIMEOUT on timeout mode", async () => {
      const adapter = new FakePushProviderAdapter({ initialMode: "timeout" });

      const result = await adapter.send({
        tenantId: "tenant-1",
        notificationId: "push-4",
        recipientId: "device_token_abc123",
        title: "Alert",
        body: "Gateway timeout",
      });

      expect(result.outcome).toBe("timeout_ambiguous");
      expect(result.failureCode).toBe("PUSH_GATEWAY_TIMEOUT");
    });
  });
});
