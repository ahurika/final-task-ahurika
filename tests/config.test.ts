import { describe, expect, it } from "vitest";
import {
  ConfigValidationError,
  loadConfig,
  type RelayConfig,
} from "../src/config";

const syntheticHmacKey = Buffer.alloc(32, 0x5a).toString("base64");

function validEnvironment(): NodeJS.ProcessEnv {
  return {
    NODE_ENV: "test",
    HOST: "127.0.0.1",
    PORT: "3000",
    DATABASE_URL:
      "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test",
    RELAY_FINGERPRINT_HMAC_KEY: syntheticHmacKey,
    RELAY_FINGERPRINT_HMAC_KEY_VERSION: "1",
    FAKE_EMAIL_MODE: "success",
    FAKE_PUSH_MODE: "success",
  };
}

describe("loadConfig", () => {
  it("loads separated server, local database, tenant auth, and fingerprint settings", () => {
    const config: RelayConfig = loadConfig(validEnvironment());

    expect(config.server).toEqual({
      environment: "test",
      host: "127.0.0.1",
      port: 3000,
    });
    expect(config.database.connectionString).toContain("127.0.0.1");
    expect(config.tenantApiAuth.credentialSource).toBe("tenant_api_keys");
    expect(config.fingerprint.activeKeyVersion).toBe(1);
    expect(config.fingerprint.hmacKey).toEqual(Buffer.alloc(32, 0x5a));
    expect(config.fakeProviders).toEqual({
      emailMode: "success",
      pushMode: "success",
    });
  });

  it("rejects missing secrets and invalid versions without echoing secret values", () => {
    const environment = validEnvironment();
    delete environment.RELAY_FINGERPRINT_HMAC_KEY;

    expect(() => loadConfig(environment)).toThrow(ConfigValidationError);
    expect(() => loadConfig(environment)).toThrow(
      "RELAY_FINGERPRINT_HMAC_KEY is required.",
    );
    expect(() => loadConfig(validEnvironment())).not.toThrow();

    const invalidVersion = validEnvironment();
    invalidVersion.RELAY_FINGERPRINT_HMAC_KEY_VERSION = "0";
    expect(() => loadConfig(invalidVersion)).toThrow(
      "RELAY_FINGERPRINT_HMAC_KEY_VERSION must be a positive integer.",
    );
  });

  it("rejects remote database URLs and shared tenant API keys", () => {
    const remoteDatabase = validEnvironment();
    remoteDatabase.DATABASE_URL =
      "postgresql://synthetic:synthetic@db.example:5432/relay";
    expect(() => loadConfig(remoteDatabase)).toThrow(
      "DATABASE_URL must point to a named local PostgreSQL database without query parameters or fragments.",
    );

    const sharedApiKey = validEnvironment();
    sharedApiKey.RELAY_API_KEY = "synthetic-shared-key";
    expect(() => loadConfig(sharedApiKey)).toThrow(
      "RELAY_API_KEY is unsupported; tenant API credentials are stored per tenant in PostgreSQL.",
    );
  });

  it.each([
    "postgresql://synthetic:synthetic@localhost:5432/relay_test",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test",
    "postgresql://synthetic:synthetic@[::1]:5432/relay_test",
  ])("accepts explicitly allowed loopback URL %s", (databaseUrl) => {
    const environment = validEnvironment();
    environment.DATABASE_URL = databaseUrl;
    expect(loadConfig(environment).database.connectionString).toBe(databaseUrl);
  });

  it.each([
    "postgresql://synthetic:synthetic@db.example:5432/relay_test",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test?host=db.example",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test?host=localhost",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test?hostaddr=127.0.0.1",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test?host=localhost&hostaddr=127.0.0.1",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test#fragment",
    "postgres://[malformed",
  ])("rejects unsafe or malformed database URL %s", (databaseUrl) => {
    const environment = validEnvironment();
    environment.DATABASE_URL = databaseUrl;
    expect(() => loadConfig(environment)).toThrow(ConfigValidationError);
  });

  it("rejects invalid server settings and fake-provider modes", () => {
    const invalidPort = validEnvironment();
    invalidPort.PORT = "65536";
    expect(() => loadConfig(invalidPort)).toThrow(
      "PORT must be an integer from 1 to 65535.",
    );

    const invalidHost = validEnvironment();
    invalidHost.HOST = "";
    expect(() => loadConfig(invalidHost)).toThrow(
      "HOST must be a non-empty host name.",
    );

    const invalidFakeMode = validEnvironment();
    invalidFakeMode.FAKE_PUSH_MODE = "random";
    expect(() => loadConfig(invalidFakeMode)).toThrow(
      "FAKE_PUSH_MODE must be success, retryable_failure, permanent_failure, or timeout_ambiguous.",
    );
  });

  it("loads only older versioned HMAC keys", () => {
    const environment = validEnvironment();
    const previousKey = Buffer.alloc(32, 0x6b);
    environment.RELAY_FINGERPRINT_HMAC_KEY_VERSION = "2";
    environment.RELAY_FINGERPRINT_HMAC_KEY_V1 =
      previousKey.toString("base64");

    expect(loadConfig(environment).fingerprint.previousKeys.get(1)).toEqual(
      previousKey,
    );

    environment.RELAY_FINGERPRINT_HMAC_KEY_V2 = syntheticHmacKey;
    expect(() => loadConfig(environment)).toThrow(
      "RELAY_FINGERPRINT_HMAC_KEY_V2 must identify a version older than the active HMAC key.",
    );
  });

  it.each([
    Buffer.alloc(31, 0x5a).toString("base64"),
    `${syntheticHmacKey.slice(0, -1)}!`,
    Buffer.alloc(32, 0xff).toString("base64").replace(/\//g, "_"),
    syntheticHmacKey.replace(/=$/, ""),
  ])("rejects invalid HMAC key encoding or strength", (key) => {
    const environment = validEnvironment();
    environment.RELAY_FINGERPRINT_HMAC_KEY = key;
    const result = () => loadConfig(environment);
    expect(result).toThrow(
      "RELAY_FINGERPRINT_HMAC_KEY must be canonical standard Base64 encoding of at least 32 bytes.",
    );
    let errorMessage = "";
    try {
      result();
    } catch (error) {
      if (error instanceof Error) {
        errorMessage = error.message;
      }
    }
    expect(errorMessage).not.toContain(key);
  });

  it("rejects an invalid previous HMAC key", () => {
    const environment = validEnvironment();
    environment.RELAY_FINGERPRINT_HMAC_KEY_VERSION = "2";
    environment.RELAY_FINGERPRINT_HMAC_KEY_V1 =
      Buffer.alloc(16, 0x6b).toString("base64");

    expect(() => loadConfig(environment)).toThrow(
      "RELAY_FINGERPRINT_HMAC_KEY_V1 must be canonical standard Base64 encoding of at least 32 bytes.",
    );
  });
});
