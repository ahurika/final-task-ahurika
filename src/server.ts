import { buildApp } from "./app";
import { loadConfig } from "./config";
import { createDatabasePool } from "./database/pool";

async function startServer(): Promise<void> {
  const config = loadConfig();
  const pool = createDatabasePool(config.database);
  const app = buildApp({
    pool,
    fingerprintConfig: config.fingerprint,
  });
  let shutdownStarted = false;

  app.addHook("onClose", async () => {
    process.removeListener("SIGINT", shutdown);
    process.removeListener("SIGTERM", shutdown);
    await pool.end();
  });

  const shutdown = (): void => {
    if (shutdownStarted) {
      return;
    }
    shutdownStarted = true;
    void app.close().catch(() => {
      console.error("Relay shutdown failed.");
      process.exitCode = 1;
    });
  };

  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);

  try {
    await app.listen({
      host: config.server.host,
      port: config.server.port,
    });
  } catch {
    try {
      await app.close();
    } catch {
      console.error("Relay startup cleanup failed.");
    }
    throw new Error("Relay server failed to start.");
  }
}

if (require.main === module) {
  void startServer().catch(() => {
    console.error("Relay startup failed.");
    process.exitCode = 1;
  });
}
