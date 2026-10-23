import crypto from "node:crypto";
import type { NotificationPayload } from "../validation";
import {
  CANONICAL_FORMAT_VERSION,
  serializeCanonicalNotification,
} from "./canonical-serializer";

export interface RequestFingerprintResult {
  fingerprint: Buffer;
  fingerprintHex: string;
  canonicalBytes: Buffer;
  fingerprintFormatVersion: number;
  hmacKeyVersion: number;
}

/**
 * Computes the HMAC-SHA-256 request fingerprint over the canonical serialized binary buffer.
 *
 * Security:
 * - Uses the dedicated fingerprint secret, NEVER tenant API keys.
 * - Secret key material is never logged, serialized, or exposed in return structures.
 * - Output is a fixed-length 32-byte cryptographic digest.
 * - Tracks format version (1) and HMAC secret version explicitly for future rotation.
 */
export function computeRequestFingerprint(
  payload: NotificationPayload,
  hmacKey: Buffer,
  hmacKeyVersion: number,
): RequestFingerprintResult {
  if (!Buffer.isBuffer(hmacKey) || hmacKey.length < 32) {
    throw new Error(
      "Fingerprint HMAC key must be a Buffer containing at least 32 bytes.",
    );
  }

  if (!Number.isInteger(hmacKeyVersion) || hmacKeyVersion <= 0) {
    throw new Error(
      "Fingerprint HMAC key version must be a positive integer.",
    );
  }

  const canonicalBytes = serializeCanonicalNotification(payload);

  const fingerprint = crypto
    .createHmac("sha256", hmacKey)
    .update(canonicalBytes)
    .digest();

  return {
    fingerprint,
    fingerprintHex: fingerprint.toString("hex"),
    canonicalBytes,
    fingerprintFormatVersion: CANONICAL_FORMAT_VERSION,
    hmacKeyVersion,
  };
}

/**
 * Constant-time verification of an expected request fingerprint against a payload.
 * Used during idempotent request replay and conflict detection.
 */
export function verifyRequestFingerprint(
  payload: NotificationPayload,
  expectedFingerprint: Buffer,
  hmacKey: Buffer,
): boolean {
  if (
    !Buffer.isBuffer(expectedFingerprint) ||
    expectedFingerprint.length !== 32
  ) {
    return false;
  }

  if (!Buffer.isBuffer(hmacKey) || hmacKey.length < 32) {
    return false;
  }

  const canonicalBytes = serializeCanonicalNotification(payload);
  const computed = crypto
    .createHmac("sha256", hmacKey)
    .update(canonicalBytes)
    .digest();

  return crypto.timingSafeEqual(computed, expectedFingerprint);
}

/**
 * Resolves the appropriate HMAC secret Buffer for a given recorded key version.
 *
 * Rules:
 * - If targetVersion matches activeKeyVersion, returns activeKey.
 * - If targetVersion matches an entry in previousKeys, returns the corresponding Buffer.
 * - If targetVersion is not found, returns null.
 *
 * CRITICAL RULE:
 * Never fall back silently to the active key if targetVersion is unknown.
 * Failing to locate the recorded key version must be treated as a configuration error
 * so that an invalid digest is never computed against a stale reservation.
 */
export function resolveFingerprintKey(
  targetVersion: number,
  activeKey: Buffer,
  activeKeyVersion: number,
  previousKeys: ReadonlyMap<number, Buffer>,
): Buffer | null {
  if (!Number.isInteger(targetVersion) || targetVersion <= 0) {
    return null;
  }

  if (targetVersion === activeKeyVersion) {
    return activeKey;
  }

  return previousKeys.get(targetVersion) ?? null;
}
