import { ValidationError } from "./notification-payload";

export const MIN_IDEMPOTENCY_KEY_BYTES = 1;
export const MAX_IDEMPOTENCY_KEY_BYTES = 128;

/**
 * Validates the `Idempotency-Key` header strictly according to DESIGN.md Section 1.4 & Section 3:
 * - Must be present and non-empty.
 * - Exactly one header permitted (rejects duplicate headers represented as arrays).
 * - Must contain only printable ASCII bytes from 0x21 through 0x7E ('!' to '~').
 * - Spaces (0x20), control characters (0x00-0x1F, 0x7F), and non-ASCII (>= 0x80) are rejected.
 * - Length between 1 and 128 bytes inclusive.
 * - Input is NEVER trimmed, transformed, or normalized.
 * - The raw key is NEVER included in error messages.
 */
export function validateIdempotencyKey(rawHeader: unknown): string {
  if (rawHeader === undefined || rawHeader === null || rawHeader === "") {
    throw new ValidationError("Idempotency-Key header is required.");
  }

  if (Array.isArray(rawHeader)) {
    throw new ValidationError(
      "Duplicate Idempotency-Key headers are not permitted.",
    );
  }

  if (typeof rawHeader !== "string") {
    throw new ValidationError("Idempotency-Key header must be a string.");
  }

  const key = rawHeader;
  const byteLength = Buffer.byteLength(key, "utf8");

  if (
    byteLength < MIN_IDEMPOTENCY_KEY_BYTES ||
    byteLength > MAX_IDEMPOTENCY_KEY_BYTES
  ) {
    throw new ValidationError(
      `Idempotency-Key must be between ${MIN_IDEMPOTENCY_KEY_BYTES} and ${MAX_IDEMPOTENCY_KEY_BYTES} bytes.`,
    );
  }

  // Strict ASCII check: characters must be strictly within 0x21..0x7E inclusive
  for (let i = 0; i < key.length; i++) {
    const code = key.charCodeAt(i);
    if (code < 0x21 || code > 0x7e) {
      throw new ValidationError(
        "Idempotency-Key must contain only printable ASCII characters (0x21-0x7E) with no whitespace.",
      );
    }
  }

  // Ensure key length matches byte length (rejects multibyte sequences)
  if (key.length !== byteLength) {
    throw new ValidationError(
      "Idempotency-Key must contain only single-byte ASCII characters.",
    );
  }

  return key;
}
