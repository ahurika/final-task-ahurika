import type { Pool } from "pg";

export interface TenantApiKeyRecord {
  publicKeyId: string;
  tenantId: string;
  secretDigest: Buffer;
  createdAt: Date;
  lastUsedAt: Date | null;
  revokedAt: Date | null;
  rotationValidUntil: Date | null;
}

/**
 * Design Pattern: Repository Pattern
 * Encapsulates persistence and retrieval operations for tenant API keys.
 * Decouples authentication logic from raw SQL query execution and database drivers,
 * enabling clean unit testing without requiring a live PostgreSQL instance.
 */
export interface TenantKeyRepository {
  findByPublicKeyId(publicKeyId: string): Promise<TenantApiKeyRecord | null>;
}

export class PostgresTenantKeyRepository implements TenantKeyRepository {
  constructor(private readonly pool: Pool) {}

  /**
   * Queries tenant API key record by non-secret public key ID.
   * Uses strictly parameterized SQL ($1) to prevent SQL injection.
   * Credential secrets are never sent to or interpolated in PostgreSQL.
   */
  async findByPublicKeyId(
    publicKeyId: string,
  ): Promise<TenantApiKeyRecord | null> {
    const query = `
      SELECT
        public_key_id,
        tenant_id,
        secret_digest,
        created_at,
        last_used_at,
        revoked_at,
        rotation_valid_until
      FROM tenant_api_keys
      WHERE public_key_id = $1
    `;

    const result = await this.pool.query(query, [publicKeyId]);
    if (result.rows.length === 0) {
      return null;
    }

    const row = result.rows[0];
    let secretDigest: Buffer;

    if (Buffer.isBuffer(row.secret_digest)) {
      secretDigest = row.secret_digest;
    } else if (typeof row.secret_digest === "string") {
      const hex = row.secret_digest.startsWith("\\x")
        ? row.secret_digest.slice(2)
        : row.secret_digest;
      secretDigest = Buffer.from(hex, "hex");
    } else {
      return null;
    }

    return {
      publicKeyId: row.public_key_id,
      tenantId: row.tenant_id,
      secretDigest,
      createdAt: new Date(row.created_at),
      lastUsedAt: row.last_used_at ? new Date(row.last_used_at) : null,
      revokedAt: row.revoked_at ? new Date(row.revoked_at) : null,
      rotationValidUntil: row.rotation_valid_until
        ? new Date(row.rotation_valid_until)
        : null,
    };
  }
}

/**
 * In-memory repository implementation for unit testing and offline verification.
 */
export class InMemoryTenantKeyRepository implements TenantKeyRepository {
  private readonly records = new Map<string, TenantApiKeyRecord>();

  addRecord(record: TenantApiKeyRecord): void {
    this.records.set(record.publicKeyId, record);
  }

  removeRecord(publicKeyId: string): void {
    this.records.delete(publicKeyId);
  }

  clear(): void {
    this.records.clear();
  }

  async findByPublicKeyId(
    publicKeyId: string,
  ): Promise<TenantApiKeyRecord | null> {
    return this.records.get(publicKeyId) ?? null;
  }
}
