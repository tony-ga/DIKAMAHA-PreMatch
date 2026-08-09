import { sql } from "drizzle-orm";
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

import { env } from "@/lib/env";

let client: ReturnType<typeof postgres> | undefined;

export function database() {
  client ??= postgres(env().DATABASE_URL, {
    max: 5,
    idle_timeout: 20,
    connect_timeout: 10,
    prepare: false,
  });
  return drizzle(client);
}

export async function databaseHealth(): Promise<boolean> {
  try {
    await database().execute(sql`select 1`);
    return true;
  } catch {
    return false;
  }
}
