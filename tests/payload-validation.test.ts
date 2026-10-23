import { describe, expect, it } from "vitest";
import {
  validateNotificationPayload,
  validateIdempotencyKey,
  ValidationError,
  MAX_RECIPIENT_ID_BYTES,
  MAX_REQUEST_BODY_BYTES,
} from "../src/validation";
import { buildApp } from "../src/app";

describe("Phase 2B — Payload & Idempotency-Key Validation", () => {
  describe("validateNotificationPayload", () => {
    it("accepts a valid email payload and preserves case and whitespace", () => {
      const input = {
        channel: "email",
        recipient_id: "user_123@example.com",
        subject: " Welcome to Relay! ",
        body: "Hello User,\n\n  Thank you for joining Relay.\nBest regards.",
      };

      const result = validateNotificationPayload(input);
      expect(result.channel).toBe("email");
      expect(result.recipient_id).toBe("user_123@example.com");
      if (result.channel === "email") {
        expect(result.subject).toBe(" Welcome to Relay! "); // Whitespace preserved
        expect(result.body).toBe(
          "Hello User,\n\n  Thank you for joining Relay.\nBest regards.",
        );
      }
    });

    it("accepts a valid push payload and preserves case and whitespace", () => {
      const input = {
        channel: "push",
        recipient_id: "device_token_xyz987",
        title: "New Alert",
        body: "Your verification code is 123456.",
      };

      const result = validateNotificationPayload(input);
      expect(result.channel).toBe("push");
      expect(result.recipient_id).toBe("device_token_xyz987");
      if (result.channel === "push") {
        expect(result.title).toBe("New Alert");
        expect(result.body).toBe("Your verification code is 123456.");
      }
    });

    it("rejects non-object payloads", () => {
      expect(() => validateNotificationPayload(null)).toThrow(ValidationError);
      expect(() => validateNotificationPayload(undefined)).toThrow(
        ValidationError,
      );
      expect(() => validateNotificationPayload([])).toThrow(ValidationError);
      expect(() => validateNotificationPayload("string")).toThrow(
        ValidationError,
      );
      expect(() => validateNotificationPayload(12345)).toThrow(ValidationError);
      expect(() => validateNotificationPayload(true)).toThrow(ValidationError);
    });

    it("rejects missing, invalid, or unsupported channel", () => {
      expect(() =>
        validateNotificationPayload({
          recipient_id: "user1",
          subject: "Sub",
          body: "Body",
        }),
      ).toThrow("Field 'channel' is required and must be a string.");

      expect(() =>
        validateNotificationPayload({
          channel: "sms",
          recipient_id: "user1",
          body: "Body",
        }),
      ).toThrow("Field 'channel' must be exactly 'email' or 'push'.");

      expect(() =>
        validateNotificationPayload({
          channel: 123,
          recipient_id: "user1",
          body: "Body",
        }),
      ).toThrow("Field 'channel' is required and must be a string.");
    });

    it("rejects missing or empty recipient_id", () => {
      expect(() =>
        validateNotificationPayload({
          channel: "email",
          subject: "Sub",
          body: "Body",
        }),
      ).toThrow("Field 'recipient_id' is required and must be a string.");

      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "",
          subject: "Sub",
          body: "Body",
        }),
      ).toThrow("Field 'recipient_id' must be between 1 and 256 UTF-8 bytes.");
    });

    it("validates recipient_id byte length boundary (1 to 256 bytes)", () => {
      // 1 byte recipient_id
      const minValid = validateNotificationPayload({
        channel: "email",
        recipient_id: "a",
        subject: "Sub",
        body: "Body",
      });
      expect(minValid.recipient_id).toBe("a");

      // 256 bytes ASCII recipient_id
      const maxAscii = "x".repeat(MAX_RECIPIENT_ID_BYTES);
      const maxValid = validateNotificationPayload({
        channel: "email",
        recipient_id: maxAscii,
        subject: "Sub",
        body: "Body",
      });
      expect(maxValid.recipient_id).toBe(maxAscii);

      // 257 bytes ASCII recipient_id -> rejected
      const exceedsAscii = "x".repeat(MAX_RECIPIENT_ID_BYTES + 1);
      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: exceedsAscii,
          subject: "Sub",
          body: "Body",
        }),
      ).toThrow("Field 'recipient_id' must be between 1 and 256 UTF-8 bytes.");

      // Multibyte UTF-8 recipient_id (e.g. euro symbol € is 3 bytes)
      // 85 * 3 = 255 bytes (valid)
      const validMultibyte = "€".repeat(85);
      expect(Buffer.byteLength(validMultibyte, "utf8")).toBe(255);
      const res = validateNotificationPayload({
        channel: "email",
        recipient_id: validMultibyte,
        subject: "Sub",
        body: "Body",
      });
      expect(res.recipient_id).toBe(validMultibyte);

      // 86 * 3 = 258 bytes (exceeds 256 bytes limit despite char count 86)
      const exceedsMultibyte = "€".repeat(86);
      expect(Buffer.byteLength(exceedsMultibyte, "utf8")).toBe(258);
      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: exceedsMultibyte,
          subject: "Sub",
          body: "Body",
        }),
      ).toThrow("Field 'recipient_id' must be between 1 and 256 UTF-8 bytes.");
    });

    it("preserves recipient_id exactly without lowercasing, trimming, or normalization", () => {
      const rawRecipient = " User.Name_123@Domain.COM ";
      const result = validateNotificationPayload({
        channel: "email",
        recipient_id: rawRecipient,
        subject: "Sub",
        body: "Body",
      });
      expect(result.recipient_id).toBe(rawRecipient);
    });

    it("rejects missing, null, empty, or whitespace-only email subject and body", () => {
      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "user1",
          body: "Body",
        }),
      ).toThrow("Field 'subject' is required for email notifications");

      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "user1",
          subject: "",
          body: "Body",
        }),
      ).toThrow("Field 'subject' cannot be empty or whitespace-only.");

      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "user1",
          subject: "   \t  \n ",
          body: "Body",
        }),
      ).toThrow("Field 'subject' cannot be empty or whitespace-only.");

      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "user1",
          subject: "Subject",
          body: "",
        }),
      ).toThrow("Field 'body' cannot be empty or whitespace-only.");

      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "user1",
          subject: "Subject",
          body: "    ",
        }),
      ).toThrow("Field 'body' cannot be empty or whitespace-only.");
    });

    it("rejects unknown or unexpected fields in email payload", () => {
      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "user1",
          subject: "Sub",
          body: "Body",
          title: "Push Title",
        }),
      ).toThrow("Unexpected field 'title' in email notification payload.");

      expect(() =>
        validateNotificationPayload({
          channel: "email",
          recipient_id: "user1",
          subject: "Sub",
          body: "Body",
          template_id: "tmpl_1",
        }),
      ).toThrow(
        "Unexpected field 'template_id' in email notification payload.",
      );
    });

    it("rejects missing, null, empty, or whitespace-only push title and body", () => {
      expect(() =>
        validateNotificationPayload({
          channel: "push",
          recipient_id: "user1",
          body: "Body",
        }),
      ).toThrow("Field 'title' is required for push notifications");

      expect(() =>
        validateNotificationPayload({
          channel: "push",
          recipient_id: "user1",
          title: "",
          body: "Body",
        }),
      ).toThrow("Field 'title' cannot be empty or whitespace-only.");

      expect(() =>
        validateNotificationPayload({
          channel: "push",
          recipient_id: "user1",
          title: "   \n\t ",
          body: "Body",
        }),
      ).toThrow("Field 'title' cannot be empty or whitespace-only.");

      expect(() =>
        validateNotificationPayload({
          channel: "push",
          recipient_id: "user1",
          title: "Alert",
          body: "",
        }),
      ).toThrow("Field 'body' cannot be empty or whitespace-only.");

      expect(() =>
        validateNotificationPayload({
          channel: "push",
          recipient_id: "user1",
          title: "Alert",
          body: "   ",
        }),
      ).toThrow("Field 'body' cannot be empty or whitespace-only.");
    });

    it("rejects unknown or unexpected fields in push payload", () => {
      expect(() =>
        validateNotificationPayload({
          channel: "push",
          recipient_id: "user1",
          title: "Title",
          body: "Body",
          subject: "Email Subject",
        }),
      ).toThrow("Unexpected field 'subject' in push notification payload.");
    });
  });

  describe("validateIdempotencyKey", () => {
    it("accepts valid 1-byte and 128-byte ASCII idempotency keys", () => {
      expect(validateIdempotencyKey("!")).toBe("!"); // 0x21
      expect(validateIdempotencyKey("~")).toBe("~"); // 0x7E

      const key128 = "A".repeat(128);
      expect(validateIdempotencyKey(key128)).toBe(key128);
    });

    it("accepts typical client idempotency keys without modifying them", () => {
      const key = "client-req-uuid-1234-5678_ABC.xyz";
      expect(validateIdempotencyKey(key)).toBe(key);
    });

    it("rejects missing, undefined, null, or empty idempotency key headers", () => {
      expect(() => validateIdempotencyKey(undefined)).toThrow(
        "Idempotency-Key header is required.",
      );
      expect(() => validateIdempotencyKey(null)).toThrow(
        "Idempotency-Key header is required.",
      );
      expect(() => validateIdempotencyKey("")).toThrow(
        "Idempotency-Key header is required.",
      );
    });

    it("rejects duplicate idempotency headers represented as arrays", () => {
      expect(() => validateIdempotencyKey(["key-1", "key-2"])).toThrow(
        "Duplicate Idempotency-Key headers are not permitted.",
      );
    });

    it("rejects keys longer than 128 bytes", () => {
      const key129 = "A".repeat(129);
      expect(() => validateIdempotencyKey(key129)).toThrow(
        "Idempotency-Key must be between 1 and 128 bytes.",
      );
    });

    it("rejects keys containing ASCII space (0x20)", () => {
      expect(() => validateIdempotencyKey("key with space")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters (0x21-0x7E) with no whitespace.",
      );
      expect(() => validateIdempotencyKey(" key")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters (0x21-0x7E) with no whitespace.",
      );
      expect(() => validateIdempotencyKey("key ")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters (0x21-0x7E) with no whitespace.",
      );
    });

    it("rejects keys containing control characters (0x00-0x1F, 0x7F)", () => {
      expect(() => validateIdempotencyKey("key\nbreak")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
      expect(() => validateIdempotencyKey("key\tbreak")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
      expect(() => validateIdempotencyKey("key\rbreak")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
      expect(() => validateIdempotencyKey("key\x00null")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
      expect(() => validateIdempotencyKey("key\x7Fdel")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
    });

    it("rejects non-ASCII and multibyte characters", () => {
      expect(() => validateIdempotencyKey("key-café")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
      expect(() => validateIdempotencyKey("key-€100")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
      expect(() => validateIdempotencyKey("key-\u00A0nonbreak")).toThrow(
        "Idempotency-Key must contain only printable ASCII characters",
      );
    });

    it("never includes the raw idempotency key value in error messages", () => {
      const sensitiveCanaryKey = "CANARY_SENSITIVE_KEY_VALUE_12345 ";
      try {
        validateIdempotencyKey(sensitiveCanaryKey);
        expect.unreachable();
      } catch (err) {
        expect((err as Error).message).not.toContain("CANARY_SENSITIVE_KEY");
      }
    });
  });

  describe("Fastify HTTP Request Body Limit (64 KiB Enforcement)", () => {
    const app = buildApp();

    // Register a test route that parses the payload using the validator
    app.post("/test/validate-payload", async (request) => {
      const idempotencyKey = validateIdempotencyKey(
        request.headers["idempotency-key"],
      );
      const validated = validateNotificationPayload(request.body);
      return {
        idempotencyKey,
        channel: validated.channel,
        recipient_id: validated.recipient_id,
      };
    });

    it("accepts requests within the 64 KiB body limit", async () => {
      const body = {
        channel: "email",
        recipient_id: "user_valid",
        subject: "Subject",
        body: "X".repeat(1000), // ~1 KB
      };

      const response = await app.inject({
        method: "POST",
        url: "/test/validate-payload",
        headers: {
          "content-type": "application/json",
          "idempotency-key": "valid-key-1",
        },
        payload: body,
      });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual({
        idempotencyKey: "valid-key-1",
        channel: "email",
        recipient_id: "user_valid",
      });
    });

    it("rejects requests exceeding the 64 KiB body limit with 413 Payload Too Large", async () => {
      // Create body exceeding 65,536 bytes
      const largeBody = {
        channel: "email",
        recipient_id: "user_large",
        subject: "Subject",
        body: "A".repeat(MAX_REQUEST_BODY_BYTES + 500),
      };

      const response = await app.inject({
        method: "POST",
        url: "/test/validate-payload",
        headers: {
          "content-type": "application/json",
          "idempotency-key": "valid-key-2",
        },
        payload: largeBody,
      });

      expect(response.statusCode).toBe(413);
      expect(response.json()).toEqual({
        statusCode: 413,
        error: "Payload Too Large",
        message: "Request payload exceeds 64 KiB limit.",
      });
    });

    it("handles validation errors with 400 Bad Request and sanitized messages", async () => {
      const invalidBody = {
        channel: "invalid_channel",
        recipient_id: "user",
      };

      const response = await app.inject({
        method: "POST",
        url: "/test/validate-payload",
        headers: {
          "content-type": "application/json",
          "idempotency-key": "valid-key-3",
        },
        payload: invalidBody,
      });

      expect(response.statusCode).toBe(400);
      expect(response.json()).toEqual({
        statusCode: 400,
        error: "Bad Request",
        message: "Field 'channel' must be exactly 'email' or 'push'.",
      });
    });

    it("handles malformed JSON syntax safely with 400 Bad Request", async () => {
      const response = await app.inject({
        method: "POST",
        url: "/test/validate-payload",
        headers: {
          "content-type": "application/json",
          "idempotency-key": "valid-key-4",
        },
        payload: '{"channel": "email", invalid-json',
      });

      expect(response.statusCode).toBe(400);
      expect(response.json()).toEqual(
        expect.objectContaining({
          statusCode: 400,
          error: "Bad Request",
        }),
      );
    });
  });
});
