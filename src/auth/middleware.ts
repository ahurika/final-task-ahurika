import type { IncomingHttpHeaders } from "node:http";
import type {
  FastifyReply,
  FastifyRequest,
  preHandlerHookHandler,
} from "fastify";
import { parseApiKey, verifySecretDigest } from "./api-key";
import type { TenantApiKeyRecord, TenantKeyRepository } from "./tenant-store";

declare module "fastify" {
  interface FastifyRequest {
    tenantId?: string;
  }
}

/**
 * Standard generic 401 response object.
 * Required to be identical across all authentication failure modes:
 * missing, malformed, unknown, incorrect secret, inactive, or revoked.
 */
export const GENERIC_UNAUTHORIZED_RESPONSE = Object.freeze({
  statusCode: 401,
  error: "Unauthorized",
  message: "Unauthorized",
});

/**
 * Extracts raw API key from incoming HTTP headers.
 * Supports:
/**
 * Extracts raw API key from incoming HTTP headers.
 * Canonical header strategy:
 * - `Authorization: Bearer <key>`
 * Supported alternatives:
 * - `Authorization: <key>`
 * - `X-API-Key: <key>`
 *
 * Security rules:
 * - Rejects requests containing multiple credential headers (e.g. both Authorization and X-API-Key).
 * - Rejects duplicate credential header arrays.
 * - Does NOT trim credentials; whitespace preservation ensures strict downstream rejection.
 */
export function extractRawApiKey(
  headers: IncomingHttpHeaders,
): string | null {
  const authHeader = headers["authorization"];
  const apiKeyHeader = headers["x-api-key"];

  // Reject duplicate headers (passed as arrays by Node HTTP parser)
  if (Array.isArray(authHeader) || Array.isArray(apiKeyHeader)) {
    return null;
  }

  const hasAuth = typeof authHeader === "string" && authHeader.length > 0;
  const hasApiKey = typeof apiKeyHeader === "string" && apiKeyHeader.length > 0;

  // Ambiguous: multiple credential headers present in a single request
  if (hasAuth && hasApiKey) {
    return null;
  }

  if (hasAuth) {
    const bearerMatch = authHeader.match(/^bearer\s(.*)$/i);
    if (bearerMatch) {
      return bearerMatch[1];
    }
    if (authHeader.startsWith("relay_live_")) {
      return authHeader;
    }
    return null;
  }

  if (hasApiKey) {
    return apiKeyHeader;
  }

  return null;
}

/**
 * Checks whether a tenant API key record is currently active:
 * 1. Must not have been revoked (revokedAt must be null).
 * 2. If rotation_valid_until is set (during planned 24-hour key rotation overlap),
 *    the current time must not exceed rotation_valid_until.
 * 3. Secret digest must be a valid 32-byte Buffer.
 */
export function isKeyActive(
  record: TenantApiKeyRecord,
  now: Date = new Date(),
): boolean {
  if (record.revokedAt !== null) {
    return false;
  }

  if (record.rotationValidUntil !== null) {
    const validUntilMs = record.rotationValidUntil.getTime();
    if (Number.isNaN(validUntilMs) || now.getTime() > validUntilMs) {
      return false;
    }
  }

  if (!Buffer.isBuffer(record.secretDigest) || record.secretDigest.length !== 32) {
    return false;
  }

  if (!record.tenantId) {
    return false;
  }

  return true;
}

export function sendUnauthorized(reply: FastifyReply): void {
  reply
    .status(401)
    .header("content-type", "application/json; charset=utf-8")
    .send(GENERIC_UNAUTHORIZED_RESPONSE);
}

/**
 * Factory creating the Fastify authentication preHandler hook.
 * Strictly verifies tenant API key and attaches the authenticated `tenant_id`
 * to `request.tenantId`. Never trusts caller-supplied tenant identifiers.
 */
export function createTenantAuthHook(
  repository: TenantKeyRepository,
): preHandlerHookHandler {
  // 32 zero bytes used for constant-time comparison when public_key_id is not found
  const DUMMY_DIGEST = Buffer.alloc(32, 0);

  return async function authenticateTenant(
    request: FastifyRequest,
    reply: FastifyReply,
  ): Promise<void> {
    const rawKey = extractRawApiKey(request.headers);
    if (!rawKey) {
      sendUnauthorized(reply);
      return;
    }

    const parsed = parseApiKey(rawKey);
    if (!parsed) {
      sendUnauthorized(reply);
      return;
    }

    const record = await repository.findByPublicKeyId(parsed.publicKeyId);
    if (!record) {
      // Execute dummy constant-time verification to mitigate timing enumeration on public_key_id
      verifySecretDigest(parsed.secret, DUMMY_DIGEST);
      sendUnauthorized(reply);
      return;
    }

    const secretValid = verifySecretDigest(parsed.secret, record.secretDigest);
    const active = isKeyActive(record);

    if (!secretValid || !active) {
      sendUnauthorized(reply);
      return;
    }

    // Attach verified tenant scope to request context
    request.tenantId = record.tenantId;
  };
}
