import { sql } from "drizzle-orm";
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

import { env } from "@/lib/env";

let client: ReturnType<typeof postgres> | undefined;

export function database() {
  return drizzle(rawDatabase());
}

/**
 * Cliente `postgres` sin envolver.
 *
 * Lo necesita el cobro: `lib/billing/apply.ts` se escribe contra este cliente
 * para que el endpoint interno de la Mini App y el reconciliador del worker
 * ejecuten **la misma** implementación. El worker no puede usar `database()`
 * porque no importa `lib/`: tiene su propia configuración y abre su propio
 * pool. Aplicar un pago son tres escrituras acopladas, y dos versiones de esa
 * transacción divergirían con el modo de fallo "pagó y no es premium".
 */
export function rawDatabase() {
  client ??= postgres(env().DATABASE_URL, {
    max: 5,
    idle_timeout: 20,
    connect_timeout: 10,
    prepare: false,
  });
  return client;
}

export async function databaseHealth(): Promise<boolean> {
  try {
    await database().execute(sql`select 1`);
    return true;
  } catch {
    return false;
  }
}
