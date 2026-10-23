const { spawn } = require("node:child_process");
const path = require("node:path");
const {
  LocalPostgresUrlError,
  runAfterLocalPostgresUrlValidation,
} = require("./local-postgres-url");

function main() {
  try {
    runAfterLocalPostgresUrlValidation(process.env.DATABASE_URL, () => {
      const migrationCli = path.join(
        process.cwd(),
        "node_modules",
        "node-pg-migrate",
        "bin",
        "node-pg-migrate.js",
      );
      const child = spawn(
        process.execPath,
        [
          migrationCli,
          "up",
          "--migrations-dir",
          "migrations",
          "--database-url-var",
          "DATABASE_URL",
        ],
        { stdio: "inherit", env: process.env },
      );

      child.on("error", () => {
        console.error("Migration command failed to start.");
        process.exitCode = 1;
      });

      child.on("exit", (code, signal) => {
        if (signal !== null) {
          console.error("Migration command was interrupted.");
          process.exitCode = 1;
        } else if (code !== 0) {
          process.exitCode = code ?? 1;
        }
      });
    });
  } catch (error) {
    if (!(error instanceof LocalPostgresUrlError)) {
      console.error("Migration command failed to start.");
      process.exitCode = 1;
      return;
    }
    console.error(
      "Migration stopped: DATABASE_URL must point to a named local PostgreSQL database without query parameters or fragments.",
    );
    process.exitCode = 1;
  }
}

if (require.main === module) {
  main();
}
