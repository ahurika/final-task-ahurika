import Fastify, {
  type FastifyError,
  type FastifyInstance,
  type preHandlerHookHandler,
} from "fastify";
import type { Pool } from "pg";
import {
  createTenantAuthHook,
  type TenantKeyRepository,
  PostgresTenantKeyRepository,
  GENERIC_UNAUTHORIZED_RESPONSE,
} from "./auth";
import {
  type NotificationRepository,
  type FingerprintKeyConfig,
  PostgresNotificationRepository,
  InMemoryNotificationRepository,
  registerNotificationRoutes,
} from "./notifications";

export interface AppOptions {
  pool?: Pool;
  tenantKeyRepository?: TenantKeyRepository;
  notificationRepository?: NotificationRepository;
  fingerprintConfig?: FingerprintKeyConfig;
}

class UnconfiguredTenantKeyRepository implements TenantKeyRepository {
  async findByPublicKeyId(): Promise<null> {
    return null;
  }
}

const DEFAULT_TEST_FINGERPRINT_CONFIG: FingerprintKeyConfig = {
  hmacKey: Buffer.alloc(32, 1),
  activeKeyVersion: 1,
  previousKeys: new Map(),
};

declare module "fastify" {
  interface FastifyInstance {
    authenticateTenant: preHandlerHookHandler;
  }
}

export const MAX_REQUEST_BODY_BYTES = 65536; // 64 KiB

export function buildApp(options: AppOptions = {}): FastifyInstance {
  const app = Fastify({
    bodyLimit: MAX_REQUEST_BODY_BYTES,
    routerOptions: {
      maxParamLength: 512,
    },
    logger: false,
  });

  // Sanitize all error responses so credentials, query parameters, or internal details never leak
  app.setErrorHandler((error: FastifyError, _request, reply) => {
    if (reply.statusCode === 401) {
      return reply.send(GENERIC_UNAUTHORIZED_RESPONSE);
    }

    if (
      error.statusCode === 413 ||
      error.code === "FST_ERR_CTP_BODY_TOO_LARGE"
    ) {
      return reply.status(413).send({
        statusCode: 413,
        error: "Payload Too Large",
        message: "Request payload exceeds 64 KiB limit.",
      });
    }

    const statusCode =
      typeof error.statusCode === "number" &&
      error.statusCode >= 400 &&
      error.statusCode < 600
        ? error.statusCode
        : 500;

    return reply.status(statusCode).send({
      statusCode,
      error:
        statusCode === 500
          ? "Internal Server Error"
          : statusCode === 400
            ? "Bad Request"
            : error.name,
      message:
        statusCode === 500
          ? "An internal server error occurred."
          : error.message || "Request could not be processed.",
    });
  });

  const repository: TenantKeyRepository =
    options.tenantKeyRepository ??
    (options.pool
      ? new PostgresTenantKeyRepository(options.pool)
      : new UnconfiguredTenantKeyRepository());

  const authenticateTenant = createTenantAuthHook(repository);
  app.decorate("authenticateTenant", authenticateTenant);

  // Unauthenticated liveness probe
  app.get("/health", async () => ({ status: "ok" }));

  // Protected verification endpoint for tenant authentication
  app.get(
    "/v1/auth/verify",
    { preHandler: [authenticateTenant] },
    async (request) => {
      return {
        status: "authenticated",
        tenant_id: request.tenantId,
      };
    },
  );

  const fingerprintConfig =
    options.fingerprintConfig ?? DEFAULT_TEST_FINGERPRINT_CONFIG;

  const notificationRepo: NotificationRepository =
    options.notificationRepository ??
    (options.pool
      ? new PostgresNotificationRepository(options.pool, fingerprintConfig)
      : new InMemoryNotificationRepository(fingerprintConfig));

  registerNotificationRoutes(app, notificationRepo, fingerprintConfig);

  return app;
}
