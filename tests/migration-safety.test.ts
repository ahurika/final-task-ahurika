import { describe, expect, it } from "vitest";
import { runAfterLocalPostgresUrlValidation } from "../scripts/local-postgres-url";

describe("migration database URL preflight", () => {
  it.each([
    "postgresql://synthetic:synthetic@localhost:5432/relay_test",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test",
    "postgresql://synthetic:synthetic@[::1]:5432/relay_test",
  ])("runs the migration action only for approved loopback URL %s", (url) => {
    let launched = false;

    runAfterLocalPostgresUrlValidation(url, () => {
      launched = true;
    });

    expect(launched).toBe(true);
  });

  it.each([
    "postgresql://synthetic:synthetic@db.example:5432/relay_test",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test?host=db.example",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test?hostaddr=127.0.0.1",
    "postgresql://synthetic:synthetic@127.0.0.1:5432/relay_test?host=localhost&hostaddr=127.0.0.1",
    "postgres://[malformed",
  ])("rejects URL before invoking the migration action: %s", (url) => {
    let launched = false;

    expect(() =>
      runAfterLocalPostgresUrlValidation(url, () => {
        launched = true;
      }),
    ).toThrow();
    expect(launched).toBe(false);
  });
});
