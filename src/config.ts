import {
  assertLocalPostgresUrl,
  LocalPostgresUrlError,
} from "../scripts/local-postgres-url";

const nodeEnvironments = ["development", "test", "production"] as const;
const fakeProviderModes = [
  "success",
  "retryable_failure",
  "permanent_failure",
  "timeout_ambiguous",
] as const;

export type NodeEnvironment = (typeof nodeEnvironments)[number];
export type FakeProviderMode = (typeof fakeProviderModes)[number];

export interface RelayConfig {
  server: {
    environment: NodeEnvironment;
    host: string;
    port: number;
  };
  database: {
    connectionString: string;
  };
  tenantApiAuth: {
    credentialSource: "tenant_api_keys";
  };
  fingerprint: {
    hmacKey: Buffer;
    activeKeyVersion: number;
    previousKeys: ReadonlyMap<number, Buffer>;
  };
  fakeProviders: {
    emailMode: FakeProviderMode;
    pushMode: FakeProviderMode;
  };
}

export class ConfigValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigValidationError";
  }
}

function isOneOf<const Values extends readonly string[]>(
  values: Values,
  value: string,
): value is Values[number] {
  return values.some((candidate) => candidate === value);
}

function requiredValue(environment: NodeJS.ProcessEnv, name: string): string {
  const value = environment[name];
  if (value === undefined || value.length === 0) {
    throw new ConfigValidationError(`${name} is required.`);
  }
  return value;
}

function parsePositiveInteger(value: string, name: string): number {
  if (!/^[1-9]\d*$/.test(value)) {
    throw new ConfigValidationError(`${name} must be a positive integer.`);
  }

  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) {
    throw new ConfigValidationError(`${name} must be a safe integer.`);
  }
  return parsed;
}

function parsePort(value: string | undefined): number {
  if (value === undefined) {
    return 3000;
  }

  if (!/^\d+$/.test(value)) {
    throw new ConfigValidationError("PORT must be an integer from 1 to 65535.");
  }

  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new ConfigValidationError("PORT must be an integer from 1 to 65535.");
  }
  return port;
}

function parseNodeEnvironment(value: string | undefined): NodeEnvironment {
  const environment = value ?? "development";
  if (!isOneOf(nodeEnvironments, environment)) {
    throw new ConfigValidationError(
      "NODE_ENV must be development, test, or production.",
    );
  }
  return environment;
}

function parseFakeProviderMode(
  value: string | undefined,
  name: string,
): FakeProviderMode {
  const mode = value ?? "success";
  if (!isOneOf(fakeProviderModes, mode)) {
    throw new ConfigValidationError(
      `${name} must be success, retryable_failure, permanent_failure, or timeout_ambiguous.`,
    );
  }
  return mode;
}

function parseHmacKey(value: string, name: string): Buffer {
  const base64Pattern =
    /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
  if (!base64Pattern.test(value)) {
    throw new ConfigValidationError(
      `${name} must be canonical standard Base64 encoding of at least 32 bytes.`,
    );
  }

  const decoded = Buffer.from(value, "base64");
  if (decoded.length < 32 || decoded.toString("base64") !== value) {
    throw new ConfigValidationError(
      `${name} must be canonical standard Base64 encoding of at least 32 bytes.`,
    );
  }
  return decoded;
}

function parsePreviousFingerprintKeys(
  environment: NodeJS.ProcessEnv,
  activeVersion: number,
): ReadonlyMap<number, Buffer> {
  const previousKeys = new Map<number, Buffer>();
  const keyPattern = /^RELAY_FINGERPRINT_HMAC_KEY_V([1-9]\d*)$/;

  for (const [name, value] of Object.entries(environment)) {
    const match = name.match(keyPattern);
    if (!match) {
      continue;
    }
    if (value === undefined || value.length === 0) {
      throw new ConfigValidationError(`${name} cannot be empty when set.`);
    }

    const version = parsePositiveInteger(match[1], name);
    if (version >= activeVersion) {
      throw new ConfigValidationError(
        `${name} must identify a version older than the active HMAC key.`,
      );
    }
    previousKeys.set(version, parseHmacKey(value, name));
  }

  return previousKeys;
}

export function loadConfig(
  environment: NodeJS.ProcessEnv = process.env,
): RelayConfig {
  if (environment.RELAY_API_KEY !== undefined) {
    throw new ConfigValidationError(
      "RELAY_API_KEY is unsupported; tenant API credentials are stored per tenant in PostgreSQL.",
    );
  }

  const nodeEnvironment = parseNodeEnvironment(environment.NODE_ENV);
  const activeKeyVersion = parsePositiveInteger(
    requiredValue(environment, "RELAY_FINGERPRINT_HMAC_KEY_VERSION"),
    "RELAY_FINGERPRINT_HMAC_KEY_VERSION",
  );
  if (activeKeyVersion > 32767) {
    throw new ConfigValidationError(
      "RELAY_FINGERPRINT_HMAC_KEY_VERSION must fit a positive PostgreSQL SMALLINT.",
    );
  }

  const host = environment.HOST ?? "127.0.0.1";
  if (!/^\S+$/.test(host)) {
    throw new ConfigValidationError("HOST must be a non-empty host name.");
  }

  const databaseUrl = requiredValue(environment, "DATABASE_URL");
  try {
    assertLocalPostgresUrl(databaseUrl);
  } catch (error) {
    if (!(error instanceof LocalPostgresUrlError)) {
      throw error;
    }
    throw new ConfigValidationError(
      "DATABASE_URL must point to a named local PostgreSQL database without query parameters or fragments.",
    );
  }

  return {
    server: {
      environment: nodeEnvironment,
      host,
      port: parsePort(environment.PORT),
    },
    database: {
      connectionString: databaseUrl,
    },
    tenantApiAuth: {
      credentialSource: "tenant_api_keys",
    },
    fingerprint: {
      hmacKey: parseHmacKey(
        requiredValue(environment, "RELAY_FINGERPRINT_HMAC_KEY"),
        "RELAY_FINGERPRINT_HMAC_KEY",
      ),
      activeKeyVersion,
      previousKeys: parsePreviousFingerprintKeys(
        environment,
        activeKeyVersion,
      ),
    },
    fakeProviders: {
      emailMode: parseFakeProviderMode(
        environment.FAKE_EMAIL_MODE,
        "FAKE_EMAIL_MODE",
      ),
      pushMode: parseFakeProviderMode(
        environment.FAKE_PUSH_MODE,
        "FAKE_PUSH_MODE",
      ),
    },
  };
}
