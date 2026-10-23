import { Pool } from "pg";

export interface DatabaseConfig {
  connectionString: string;
}

export function createDatabasePool(config: DatabaseConfig): Pool {
  return new Pool({
    connectionString: config.connectionString,
    application_name: "relay-local-prototype",
  });
}
