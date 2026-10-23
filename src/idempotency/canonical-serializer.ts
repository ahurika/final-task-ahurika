import type { NotificationPayload } from "../validation";

export const CANONICAL_FORMAT_PREFIX = "relay-notification-v1\0";
export const CANONICAL_FORMAT_VERSION = 1;

export const FIELD_TAG_CHANNEL = 0x01;
export const FIELD_TAG_RECIPIENT_ID = 0x02;
export const FIELD_TAG_EMAIL_SUBJECT = 0x10;
export const FIELD_TAG_EMAIL_BODY = 0x11;
export const FIELD_TAG_PUSH_TITLE = 0x20;
export const FIELD_TAG_PUSH_BODY = 0x21;

export interface NormalizedNotificationContent {
  channel: "email" | "push";
  recipientId: string;
  field1: { tag: number; value: string };
  field2: { tag: number; value: string };
}

/**
 * Normalizes notification payload content to Unicode NFC:
 * - Normalizes ONLY user-visible content:
 *   - Email: subject, body.
 *   - Push: title, body.
 * - Preserves letter case and whitespace.
 * - Preserves recipient_id exactly as decoded (NO normalization, NO trimming, NO lowercasing).
 */
export function normalizeNotificationContent(
  payload: NotificationPayload,
): NormalizedNotificationContent {
  if (payload.channel === "email") {
    return {
      channel: "email",
      recipientId: payload.recipient_id,
      field1: {
        tag: FIELD_TAG_EMAIL_SUBJECT,
        value: payload.subject.normalize("NFC"),
      },
      field2: {
        tag: FIELD_TAG_EMAIL_BODY,
        value: payload.body.normalize("NFC"),
      },
    };
  } else {
    return {
      channel: "push",
      recipientId: payload.recipient_id,
      field1: {
        tag: FIELD_TAG_PUSH_TITLE,
        value: payload.title.normalize("NFC"),
      },
      field2: {
        tag: FIELD_TAG_PUSH_BODY,
        value: payload.body.normalize("NFC"),
      },
    };
  }
}

/**
 * Encodes a single tagged binary field:
 * - 1-byte field tag
 * - 4-byte unsigned big-endian length of the UTF-8 bytes
 * - UTF-8 value bytes
 */
export function encodeTaggedField(tag: number, value: string): Buffer {
  const valueBuffer = Buffer.from(value, "utf8");
  const fieldBuffer = Buffer.allocUnsafe(1 + 4 + valueBuffer.length);

  fieldBuffer.writeUInt8(tag, 0);
  fieldBuffer.writeUInt32BE(valueBuffer.length, 1);
  valueBuffer.copy(fieldBuffer, 5);

  return fieldBuffer;
}

/**
 * Serializes a validated notification payload into the approved versioned tagged binary format.
 *
 * Structure:
 * 1. Prefix: UTF-8 `relay-notification-v1` + 0x00 (22 bytes).
 * 2. Tagged field: Channel (Tag 0x01).
 * 3. Tagged field: Recipient ID (Tag 0x02).
 * 4. Tagged field: Email subject (0x10) or Push title (0x20).
 * 5. Tagged field: Email body (0x11) or Push body (0x21).
 *
 * Excludes tenant identity, API credentials, raw idempotency key, timestamps, and headers.
 */
export function serializeCanonicalNotification(
  payload: NotificationPayload,
): Buffer {
  const normalized = normalizeNotificationContent(payload);

  const prefixBuffer = Buffer.from(CANONICAL_FORMAT_PREFIX, "utf8");
  const channelBuffer = encodeTaggedField(
    FIELD_TAG_CHANNEL,
    normalized.channel,
  );
  const recipientBuffer = encodeTaggedField(
    FIELD_TAG_RECIPIENT_ID,
    normalized.recipientId,
  );
  const field1Buffer = encodeTaggedField(
    normalized.field1.tag,
    normalized.field1.value,
  );
  const field2Buffer = encodeTaggedField(
    normalized.field2.tag,
    normalized.field2.value,
  );

  return Buffer.concat([
    prefixBuffer,
    channelBuffer,
    recipientBuffer,
    field1Buffer,
    field2Buffer,
  ]);
}
