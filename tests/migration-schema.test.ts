import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const migration = readFileSync(
  "migrations/20261022200400_initial_schema.js",
  "utf8",
);

describe("initial migration safety checks", () => {
  it("enforces channel-specific content pairing and permits terminal scrubbing", () => {
    expect(migration).toContain(
      "CONSTRAINT notifications_rendered_content_check CHECK",
    );
    expect(migration).toMatch(
      /channel = 'email'\s+AND title IS NULL\s+AND\s+\(\s+\(subject IS NULL AND body IS NULL\)\s+OR \(subject IS NOT NULL AND body IS NOT NULL\)\s+\)\)/,
    );
    expect(migration).toMatch(
      /channel = 'push'\s+AND subject IS NULL\s+AND\s+\(\s+\(title IS NULL AND body IS NULL\)\s+OR \(title IS NOT NULL AND body IS NOT NULL\)\s+\)\)/,
    );
    expect(migration).toContain(
      "CONSTRAINT notifications_active_content_check CHECK",
    );
  });

  it("requires completion timestamps exactly for completed delivery attempts", () => {
    expect(migration).toContain(
      "CONSTRAINT delivery_attempt_completion_check CHECK",
    );
    expect(migration).toMatch(
      /state = 'started' AND completed_at IS NULL[\s\S]*state <> 'started' AND completed_at IS NOT NULL/,
    );
  });

  // NOTE: The two tests below inspect migration SOURCE TEXT only.
  // They do not connect to PostgreSQL and do not prove that the database
  // engine accepts or enforces these definitions. Actual enforcement
  // must be verified after a controlled migration run against a live database.

  it("declares public_key_id as PRIMARY KEY on tenant_api_keys (source-text check)", () => {
    // public_key_id is promoted to PRIMARY KEY because it was already
    // NOT NULL UNIQUE and is the natural, design-sanctioned lookup identifier
    // for an API key record. No new column is introduced; the redundant
    // UNIQUE constraint is subsumed by the PK. No other table references
    // tenant_api_keys by FK, so no existing relationship is affected.
    expect(migration).toMatch(/public_key_id\s+TEXT\s+PRIMARY KEY/);
    expect(migration).not.toMatch(/public_key_id\s+TEXT\s+NOT NULL\s+UNIQUE/);
  });

  it("declares a partial index on outbox_events for pending rows (source-text check)", () => {
    // The worker poller queries WHERE state = 'pending' AND available_at <= NOW().
    // A partial index on available_at filtered to state = 'pending' keeps the
    // index small (claimed and completed rows are excluded) and directly
    // supports the polling query without scanning the full table.
    expect(migration).toContain("CREATE INDEX outbox_events_pending_available_idx");
    expect(migration).toMatch(
      /ON outbox_events\s+\(available_at\)\s+WHERE state = 'pending'/,
    );
  });
});
