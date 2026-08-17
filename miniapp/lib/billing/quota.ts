import { sql } from "drizzle-orm";

import { database } from "@/lib/db";
import { env } from "@/lib/env";

export type QuotaSurface = "miniapp" | "bot" | "share";

export type QuotaOutcome =
  /** Premium: no hay contador y no se toca la base. */
  | { granted: true; reason: "premium"; remaining: null }
  /** Ya había desbloqueado este partido hoy; no cuesta otra unidad. */
  | { granted: true; reason: "replay"; remaining: number }
  | { granted: true; reason: "consumed"; remaining: number }
  | { granted: false; reason: "exhausted"; remaining: 0 };

/**
 * Día local del producto.
 *
 * El mismo huso que el track record diario de la Fase 121. Si el día del
 * contador y el día del historial discreparan, "3 al día" significaría dos
 * cosas distintas según qué pantalla mire el usuario.
 */
function quotaDay() {
  return sql`(now() AT TIME ZONE ${env().MINIAPP_QUOTA_TIMEZONE})::date`;
}

export function freeDailyLimit(): number {
  return env().MINIAPP_FREE_DAILY_PREDICTIONS;
}

/**
 * Reserva una predicción del cupo diario gratuito.
 *
 * Todo el veredicto cabe en **una sola sentencia**, y por tanto en una única
 * transacción implícita: no hay ventana de leer-modificar-escribir que perder
 * ni bloqueo de aplicación que administrar mal. El guardián es el
 * `WHERE q.used < q.daily_limit` del `ON CONFLICT DO UPDATE`: el conflicto toma
 * el row lock de la fila existente, así que dos peticiones simultáneas -una de
 * la Mini App y otra del bot- se serializan ahí y la segunda evalúa `used` ya
 * incrementado. Cuando el `WHERE` no se cumple, el `DO UPDATE` devuelve cero
 * filas y **no lanza excepción**; eso es lo que distingue "agotado" de "error".
 *
 * El contador vive en PostgreSQL y no en memoria como el rate limiter de
 * `lib/http.ts` porque el bot y la Mini App tienen que ver el mismo número: el
 * cupo es por persona, no por superficie.
 */
export async function consumePrediction(
  userId: number,
  fixtureKey: string,
  surface: QuotaSurface,
  options: { premium?: boolean; limit?: number } = {},
): Promise<QuotaOutcome> {
  if (options.premium) return { granted: true, reason: "premium", remaining: null };
  const limit = options.limit ?? freeDailyLimit();

  const rows = await database().execute<{
    replay: boolean;
    used_after: number | null;
    limit_after: number | null;
  }>(sql`
    WITH day AS (SELECT ${quotaDay()} AS d),
    already AS (
      SELECT 1 FROM "prediction_grants", day
       WHERE "user_id" = ${userId}
         AND "usage_date" = day.d
         AND "fixture_key" = ${fixtureKey}
    ),
    consumed AS (
      INSERT INTO "prediction_quota_days" AS q
        ("user_id", "usage_date", "used", "daily_limit")
      SELECT ${userId}, day.d, 1, ${limit} FROM day
       WHERE NOT EXISTS (SELECT 1 FROM already)
      ON CONFLICT ("user_id", "usage_date") DO UPDATE
         SET "used" = q."used" + 1, "updated_at" = now()
       WHERE q."used" < q."daily_limit"
      RETURNING q."used", q."daily_limit"
    ),
    granted AS (
      INSERT INTO "prediction_grants"
        ("user_id", "usage_date", "fixture_key", "surface")
      SELECT ${userId}, day.d, ${fixtureKey}, ${surface} FROM day
       WHERE EXISTS (SELECT 1 FROM consumed)
      ON CONFLICT DO NOTHING
      RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM already)     AS "replay",
           (SELECT "used" FROM consumed)      AS "used_after",
           (SELECT "daily_limit" FROM consumed) AS "limit_after"
  `);

  const row = rows[0];
  if (row?.replay) {
    const snapshot = await quotaSnapshot(userId, limit);
    return {
      granted: true,
      reason: "replay",
      remaining: Math.max(snapshot.limit - snapshot.used, 0),
    };
  }
  if (row?.used_after !== null && row?.used_after !== undefined) {
    const effectiveLimit = row.limit_after ?? limit;
    return {
      granted: true,
      reason: "consumed",
      remaining: Math.max(effectiveLimit - row.used_after, 0),
    };
  }
  return { granted: false, reason: "exhausted", remaining: 0 };
}

/**
 * Devuelve al cupo una unidad que no llegó a producir nada.
 *
 * Cobrar por una predicción que el usuario nunca vio es el peor fallo posible
 * de esta función, así que el orden es reservar, llamar y liberar si la llamada
 * falla. Es idempotente: sólo decrementa si la concesión existía de verdad.
 */
export async function releasePrediction(
  userId: number,
  fixtureKey: string,
): Promise<void> {
  await database().execute(sql`
    WITH day AS (SELECT ${quotaDay()} AS d),
    removed AS (
      DELETE FROM "prediction_grants" USING day
       WHERE "user_id" = ${userId}
         AND "usage_date" = day.d
         AND "fixture_key" = ${fixtureKey}
      RETURNING 1
    )
    UPDATE "prediction_quota_days" q
       SET "used" = GREATEST(q."used" - 1, 0), "updated_at" = now()
      FROM day
     WHERE q."user_id" = ${userId}
       AND q."usage_date" = day.d
       AND EXISTS (SELECT 1 FROM removed)
  `);
}

export async function quotaSnapshot(
  userId: number,
  limit = freeDailyLimit(),
): Promise<{ used: number; limit: number; remaining: number }> {
  const rows = await database().execute<{ used: number; daily_limit: number }>(sql`
    SELECT "used", "daily_limit" FROM "prediction_quota_days"
     WHERE "user_id" = ${userId} AND "usage_date" = ${quotaDay()}
     LIMIT 1
  `);
  const row = rows[0];
  const used = Number(row?.used ?? 0);
  // El tope publicado es el congelado en la fila del día si existe: es el que
  // se le prometió al usuario esta mañana.
  const effective = Number(row?.daily_limit ?? limit);
  return { used, limit: effective, remaining: Math.max(effective - used, 0) };
}
