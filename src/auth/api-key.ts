import crypto from "node:crypto";

export const API_KEY_PREFIX = "relay_live_";

export interface ParsedApiKey {
  publicKeyId: string;
  secret: string;
}

export interface GeneratedApiKey {
  rawKey: string;
  publicKeyId: string;
  secret: string;
  secretDigest: Buffer;
}

/**
 * Strictly parses a raw tenant API key according to DESIGN.md Section 3:
 * Format: `relay_live_<public-key-id>.<secret>`
 *
 * Rules:
 * - Must begin with exact prefix `relay_live_`.
 * - Exactly one period (.) separates the public key ID and secret.
 * - Public key ID must be 1-128 ASCII characters matching [a-zA-Z0-9_-].
 * - Secret must be 16-256 characters matching [a-zA-Z0-9+/=_-] (Base64, Base64url, Hex).
 * - No whitespace, control characters, or extra dots permitted.
 * - Never trims input; strict matching only.
 */
export function parseApiKey(rawKey: unknown): ParsedApiKey | null {
  if (
    typeof rawKey !== "string" ||
    rawKey.length > 512 ||
    !rawKey.startsWith(API_KEY_PREFIX)
  ) {
    return null;
  }

  const remainder = rawKey.slice(API_KEY_PREFIX.length);
  const dotIndex = remainder.indexOf(".");
  if (dotIndex <= 0) {
    return null;
  }

  const publicKeyId = remainder.slice(0, dotIndex);
  const secret = remainder.slice(dotIndex + 1);

  if (!publicKeyId || !secret) {
    return null;
  }

  // Reject if remainder contains multiple dots
  if (secret.includes(".")) {
    return null;
  }

  // Strict format checks: no whitespace, safe characters only
  if (!/^[a-zA-Z0-9_-]{1,128}$/.test(publicKeyId)) {
    return null;
  }

  if (!/^[a-zA-Z0-9+/=_-]{16,256}$/.test(secret)) {
    return null;
  }

  return { publicKeyId, secret };
}

/**
 * Computes the SHA-256 digest of the presented secret.
 * SHA-256 is appropriate for uniformly random, high-entropy API secrets (32 bytes entropy).
 */
export function hashApiKeySecret(secret: string): Buffer {
  return crypto.createHash("sha256").update(secret, "utf8").digest();
}

/**
 * Compares the presented secret with a stored digest using constant-time comparison
 * after ensuring both buffers have matching length (32 bytes for SHA-256).
 */
export function verifySecretDigest(
  presentedSecret: string,
  storedDigest: Buffer,
): boolean {
  const presentedDigest = hashApiKeySecret(presentedSecret);

  if (
    !Buffer.isBuffer(storedDigest) ||
    presentedDigest.length !== storedDigest.length
  ) {
    return false;
  }

  return crypto.timingSafeEqual(presentedDigest, storedDigest);
}

/**
 * Generates a high-entropy tenant API key pair.
 * Intended for test fixtures and local owner key-generation tooling; NOT exposed as a public API.
 */
export function generateApiKey(customPublicKeyId?: string): GeneratedApiKey {
  const publicKeyId =
    customPublicKeyId ?? crypto.randomBytes(12).toString("hex");
  // 32 cryptographically random bytes = 256 bits of entropy
  const secretBytes = crypto.randomBytes(32);
  const secret = secretBytes.toString("base64url");
  const rawKey = `${API_KEY_PREFIX}${publicKeyId}.${secret}`;
  const secretDigest = hashApiKeySecret(secret);

  return {
    rawKey,
    publicKeyId,
    secret,
    secretDigest,
  };
}
