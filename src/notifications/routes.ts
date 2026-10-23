import type { FastifyInstance } from "fastify";
import { validateNotificationPayload, validateIdempotencyKey } from "../validation";
import { computeRequestFingerprint } from "../idempotency";
import type { NotificationRepository } from "./notification-repository";
import type { FingerprintKeyConfig } from "./types";

const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function registerNotificationRoutes(
  app: FastifyInstance,
  repository: NotificationRepository,
  fingerprintConfig: FingerprintKeyConfig,
): void {
  /**
   * POST /v1/notifications
   *
   * Authenticated notification creation endpoint.
   * Performs atomic acceptance, preference check, idempotency reservation,
   * and transactional outbox event creation.
   */
  app.post(
    "/v1/notifications",
    { preHandler: [app.authenticateTenant] },
    async (request, reply) => {
      const tenantId = request.tenantId;
      if (!tenantId) {
        return reply.status(401).send({
          statusCode: 401,
          error: "Unauthorized",
          message: "Unauthorized",
        });
      }

      // 1. Validate Idempotency-Key header strictly (printable ASCII 0x21-0x7E, 1-128 bytes, no trimming)
      const rawIdempotencyKey = request.headers["idempotency-key"];
      const idempotencyKey = validateIdempotencyKey(rawIdempotencyKey);

      // 2. Validate request payload against channel constraints
      const payload = validateNotificationPayload(request.body);

      // 3. Compute canonical tagged binary HMAC-SHA-256 request fingerprint
      const fingerprintResult = computeRequestFingerprint(
        payload,
        fingerprintConfig.hmacKey,
        fingerprintConfig.activeKeyVersion,
      );

      // 4. Execute atomic acceptance transaction
      const result = await repository.acceptNotification({
        tenantId,
        idempotencyKey,
        payload,
        fingerprintResult,
      });

      if (result.outcome === "conflict") {
        return reply.status(409).send({
          statusCode: 409,
          error: "Conflict",
          message:
            "Idempotency key has already been used with a different request payload.",
        });
      }

      if (result.outcome === "replayed") {
        return reply.status(200).send(result.notification);
      }

      // outcome === "created"
      return reply.status(202).send(result.notification);
    },
  );

  /**
   * GET /v1/notifications/:id
   *
   * Returns metadata and current status for a notification belonging to the authenticated tenant.
   * Never exposes notification content (subject/body/title) or raw idempotency keys.
   */
  app.get<{ Params: { id: string } }>(
    "/v1/notifications/:id",
    { preHandler: [app.authenticateTenant] },
    async (request, reply) => {
      const tenantId = request.tenantId;
      if (!tenantId) {
        return reply.status(401).send({
          statusCode: 401,
          error: "Unauthorized",
          message: "Unauthorized",
        });
      }

      const { id } = request.params;
      if (!UUID_REGEX.test(id)) {
        return reply.status(404).send({
          statusCode: 404,
          error: "Not Found",
          message: "Notification not found.",
        });
      }

      const notification = await repository.getNotification(tenantId, id);

      if (!notification) {
        return reply.status(404).send({
          statusCode: 404,
          error: "Not Found",
          message: "Notification not found.",
        });
      }

      return reply.status(200).send({
        id: notification.id,
        status: notification.status,
        channel: notification.channel,
        recipient_id: notification.recipient_id,
        suppression_reason: notification.suppression_reason ?? undefined,
        accepted_at: notification.accepted_at.toISOString(),
        terminal_at: notification.terminal_at?.toISOString() ?? undefined,
        attempt_count: notification.attempt_count,
      });
    },
  );

  /**
   * GET /v1/preferences/:recipientId
   * Reads notification preferences for the authenticated tenant.
   */
  app.get<{ Params: { recipientId: string }; Querystring: { channel: string } }>(
    "/v1/preferences/:recipientId",
    { preHandler: [app.authenticateTenant] },
    async (request, reply) => {
      const tenantId = request.tenantId;
      if (!tenantId) {
        return reply.status(401).send({
          statusCode: 401,
          error: "Unauthorized",
          message: "Unauthorized",
        });
      }

      const { recipientId } = request.params;
      const recipientBytes = Buffer.byteLength(recipientId, "utf8");
      if (recipientBytes === 0 || recipientBytes > 256) {
        return reply.status(400).send({
          statusCode: 400,
          error: "Bad Request",
          message: "Recipient ID must be between 1 and 256 UTF-8 bytes.",
        });
      }

      const channel = request.query.channel as "email" | "push";

      if (channel !== "email" && channel !== "push") {
        return reply.status(400).send({
          statusCode: 400,
          error: "Bad Request",
          message: "Query parameter 'channel' must be 'email' or 'push'.",
        });
      }

      const enabled = await repository.getPreference(
        tenantId,
        recipientId,
        channel,
      );

      return reply.status(200).send({
        recipient_id: recipientId,
        channel,
        enabled,
      });
    },
  );

  /**
   * PUT /v1/preferences/:recipientId
   * Updates or sets notification preferences for the authenticated tenant.
   */
  app.put<{
    Params: { recipientId: string };
    Body: { channel?: unknown; enabled?: unknown };
  }>(
    "/v1/preferences/:recipientId",
    { preHandler: [app.authenticateTenant] },
    async (request, reply) => {
      const tenantId = request.tenantId;
      if (!tenantId) {
        return reply.status(401).send({
          statusCode: 401,
          error: "Unauthorized",
          message: "Unauthorized",
        });
      }

      const { recipientId } = request.params;
      const recipientBytes = Buffer.byteLength(recipientId, "utf8");
      if (recipientBytes === 0 || recipientBytes > 256) {
        return reply.status(400).send({
          statusCode: 400,
          error: "Bad Request",
          message: "Recipient ID must be between 1 and 256 UTF-8 bytes.",
        });
      }

      const body = request.body as Record<string, unknown> | null;

      if (!body || typeof body !== "object" || Array.isArray(body)) {
        return reply.status(400).send({
          statusCode: 400,
          error: "Bad Request",
          message: "Request body must be a JSON object.",
        });
      }

      const allowedKeys = new Set(["channel", "enabled"]);
      for (const key of Object.keys(body)) {
        if (!allowedKeys.has(key)) {
          return reply.status(400).send({
            statusCode: 400,
            error: "Bad Request",
            message: `Unexpected property '${key}' in preference update.`,
          });
        }
      }

      const channel = body.channel;
      if (channel !== "email" && channel !== "push") {
        return reply.status(400).send({
          statusCode: 400,
          error: "Bad Request",
          message: "Field 'channel' must be 'email' or 'push'.",
        });
      }

      const enabled = body.enabled;
      if (typeof enabled !== "boolean") {
        return reply.status(400).send({
          statusCode: 400,
          error: "Bad Request",
          message: "Field 'enabled' must be a boolean.",
        });
      }

      await repository.setPreference(tenantId, recipientId, channel, enabled);

      return reply.status(200).send({
        recipient_id: recipientId,
        channel,
        enabled,
      });
    },
  );
}
