import postgres from "postgres";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required");

const sql = postgres(databaseUrl, { max: 1, prepare: false });
const migration = await readFile(
  resolve(process.cwd(), "drizzle/0000_phase_115_miniapp.sql"),
  "utf8",
);

await sql.unsafe(migration);
await sql.end();
console.info("phase_115_migration_applied");
