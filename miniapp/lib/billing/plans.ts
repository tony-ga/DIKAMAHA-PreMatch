import { sql } from "drizzle-orm";

import { database } from "@/lib/db";
import { env } from "@/lib/env";

export const PREMIUM_PLAN_CODE = "premium_monthly";

export type BillingPlanView = {
  code: string;
  starsAmount: number;
  periodSeconds: number;
};

/**
 * Precio vigente del nivel de pago.
 *
 * Se lee de `billing_plans` y no del entorno porque `env()` se cachea a nivel
 * de módulo: cambiar el precio por variable exigiría reiniciar el servicio. La
 * economía de Stars puede moverse y el punto de equilibrio del proyecto está en
 * ~15 suscriptores, así que ajustar el precio tiene que ser un UPDATE.
 *
 * `MINIAPP_PREMIUM_STARS_PRICE` queda sólo como semilla y como red por si la
 * fila no existiera todavía -por ejemplo entre el despliegue del código y el de
 * la migración-.
 */
export async function activePlan(): Promise<BillingPlanView> {
  const rows = await database().execute<{
    code: string; stars_amount: number; period_seconds: number;
  }>(sql`
    SELECT "code", "stars_amount", "period_seconds"
      FROM "billing_plans"
     WHERE "code" = ${PREMIUM_PLAN_CODE} AND "active" = true
     LIMIT 1
  `);
  const row = rows[0];
  if (!row) {
    return {
      code: PREMIUM_PLAN_CODE,
      starsAmount: env().MINIAPP_PREMIUM_STARS_PRICE,
      periodSeconds: 2_592_000,
    };
  }
  return {
    code: row.code,
    starsAmount: Number(row.stars_amount),
    periodSeconds: Number(row.period_seconds),
  };
}

/** Ajusta el precio en caliente. Sólo lo invoca la ruta de administración. */
export async function setPlanPrice(starsAmount: number): Promise<BillingPlanView> {
  if (!Number.isSafeInteger(starsAmount) || starsAmount < 1 || starsAmount > 10_000) {
    throw new Error("billing_price_invalid");
  }
  await database().execute(sql`
    INSERT INTO "billing_plans" ("code", "stars_amount", "period_seconds")
    VALUES (${PREMIUM_PLAN_CODE}, ${starsAmount}, 2592000)
    ON CONFLICT ("code") DO UPDATE
       SET "stars_amount" = EXCLUDED."stars_amount", "updated_at" = now()
  `);
  return activePlan();
}
