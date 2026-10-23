const { parse } = require("pg-connection-string");

const loopbackHosts = new Set(["localhost", "127.0.0.1", "::1"]);

class LocalPostgresUrlError extends Error {
  constructor(message) {
    super(message);
    this.name = "LocalPostgresUrlError";
  }
}

function normalizeHostname(hostname) {
  return hostname.replace(/^\[|\]$/g, "").toLowerCase();
}

function assertLocalPostgresUrl(value) {
  if (typeof value !== "string" || value.length === 0) {
    throw new LocalPostgresUrlError("DATABASE_URL is missing or malformed.");
  }

  let url;
  let parsed;
  try {
    url = new URL(value);
    parsed = parse(value);
  } catch {
    throw new LocalPostgresUrlError("DATABASE_URL is malformed.");
  }

  if (
    !["postgres:", "postgresql:"].includes(url.protocol) ||
    value.includes("?") ||
    value.includes("#") ||
    !url.pathname ||
    url.pathname === "/"
  ) {
    throw new LocalPostgresUrlError(
      "DATABASE_URL is not an allowed local PostgreSQL URL.",
    );
  }

  const authorityHost = normalizeHostname(url.hostname);
  const effectiveHost = normalizeHostname(parsed.host ?? "");
  if (
    !loopbackHosts.has(authorityHost) ||
    !loopbackHosts.has(effectiveHost) ||
    authorityHost !== effectiveHost ||
    !parsed.database
  ) {
    throw new LocalPostgresUrlError(
      "DATABASE_URL does not resolve to an approved loopback host.",
    );
  }

  return value;
}

function runAfterLocalPostgresUrlValidation(value, action) {
  assertLocalPostgresUrl(value);
  return action(value);
}

module.exports = {
  LocalPostgresUrlError,
  assertLocalPostgresUrl,
  runAfterLocalPostgresUrlValidation,
};
