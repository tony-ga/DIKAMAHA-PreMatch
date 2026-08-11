import postgres from "postgres";
import { readFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";

const databaseUrl = process.env.DATABASE_URL;
if (!databaseUrl) throw new Error("DATABASE_URL is required");

const directory = resolve(process.cwd(), "drizzle");
const files = (await readdir(directory))
  .filter((name) => name.endsWith(".sql"))
  .sort();

const sql = postgres(databaseUrl, { max: 1, prepare: false });
for (const name of files) {
  const migration = await readFile(resolve(directory, name), "utf8");
  await sql.unsafe(migration);
  console.info(`migration_applied ${name}`);
}
await sql.end();
console.info(`migrations_complete ${files.length}`);
