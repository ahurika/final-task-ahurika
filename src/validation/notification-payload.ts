export type NotificationChannel = "email" | "push";

export interface EmailNotificationPayload {
  channel: "email";
  recipient_id: string;
  subject: string;
  body: string;
}

export interface PushNotificationPayload {
  channel: "push";
  recipient_id: string;
  title: string;
  body: string;
}

export type NotificationPayload =
  | EmailNotificationPayload
  | PushNotificationPayload;

export class ValidationError extends Error {
  public readonly statusCode = 400;

  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

export const MAX_REQUEST_BODY_BYTES = 65536; // 64 KiB
export const MAX_RECIPIENT_ID_BYTES = 256;

const ALLOWED_EMAIL_KEYS = new Set([
  "channel",
  "recipient_id",
  "subject",
  "body",
]);

const ALLOWED_PUSH_KEYS = new Set([
  "channel",
  "recipient_id",
  "title",
  "body",
]);

/**
 * Validates raw parsed JSON against Relay notification request contract.
 *
 * Rules:
 * - Must be a non-null JSON object (not an array, not null).
 * - `channel` must be exactly "email" or "push".
 * - `recipient_id` must be 1-256 UTF-8 bytes; preserved exactly as decoded.
 * - Email requires non-empty, non-whitespace-only `subject` and `body`.
 * - Push requires non-empty, non-whitespace-only `title` and `body`.
 * - Unknown or unexpected fields are strictly rejected.
 * - Message whitespace and letter case are preserved.
 */
export function validateNotificationPayload(
  payload: unknown,
): NotificationPayload {
  if (
    typeof payload !== "object" ||
    payload === null ||
    Array.isArray(payload)
  ) {
    throw new ValidationError("Request payload must be a JSON object.");
  }

  const record = payload as Record<string, unknown>;

  // Check channel
  if (typeof record.channel !== "string") {
    throw new ValidationError(
      "Field 'channel' is required and must be a string.",
    );
  }

  const channel = record.channel;
  if (channel !== "email" && channel !== "push") {
    throw new ValidationError(
      "Field 'channel' must be exactly 'email' or 'push'.",
    );
  }

  // Check recipient_id
  if (typeof record.recipient_id !== "string") {
    throw new ValidationError(
      "Field 'recipient_id' is required and must be a string.",
    );
  }

  const recipientId = record.recipient_id;
  const recipientBytes = Buffer.byteLength(recipientId, "utf8");
  if (recipientBytes < 1 || recipientBytes > MAX_RECIPIENT_ID_BYTES) {
    throw new ValidationError(
      `Field 'recipient_id' must be between 1 and ${MAX_RECIPIENT_ID_BYTES} UTF-8 bytes.`,
    );
  }

  // Reject unexpected keys and validate channel-specific content
  const keys = Object.keys(record);

  if (channel === "email") {
    for (const key of keys) {
      if (!ALLOWED_EMAIL_KEYS.has(key)) {
        throw new ValidationError(
          `Unexpected field '${key}' in email notification payload.`,
        );
      }
    }

    if (typeof record.subject !== "string") {
      throw new ValidationError(
        "Field 'subject' is required for email notifications and must be a string.",
      );
    }
    if (record.subject.length === 0 || record.subject.trim().length === 0) {
      throw new ValidationError(
        "Field 'subject' cannot be empty or whitespace-only.",
      );
    }

    if (typeof record.body !== "string") {
      throw new ValidationError(
        "Field 'body' is required for email notifications and must be a string.",
      );
    }
    if (record.body.length === 0 || record.body.trim().length === 0) {
      throw new ValidationError(
        "Field 'body' cannot be empty or whitespace-only.",
      );
    }

    return {
      channel: "email",
      recipient_id: recipientId,
      subject: record.subject,
      body: record.body,
    };
  } else {
    for (const key of keys) {
      if (!ALLOWED_PUSH_KEYS.has(key)) {
        throw new ValidationError(
          `Unexpected field '${key}' in push notification payload.`,
        );
      }
    }

    if (typeof record.title !== "string") {
      throw new ValidationError(
        "Field 'title' is required for push notifications and must be a string.",
      );
    }
    if (record.title.length === 0 || record.title.trim().length === 0) {
      throw new ValidationError(
        "Field 'title' cannot be empty or whitespace-only.",
      );
    }

    if (typeof record.body !== "string") {
      throw new ValidationError(
        "Field 'body' is required for push notifications and must be a string.",
      );
    }
    if (record.body.length === 0 || record.body.trim().length === 0) {
      throw new ValidationError(
        "Field 'body' cannot be empty or whitespace-only.",
      );
    }

    return {
      channel: "push",
      recipient_id: recipientId,
      title: record.title,
      body: record.body,
    };
  }
}
