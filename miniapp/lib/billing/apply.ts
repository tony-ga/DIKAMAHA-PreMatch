import type { Sql } from "postgres";

/**
 * Aplicación de cargos y reembolsos Stars.
 *
 * Escrito contra el cliente `postgres` crudo y no contra Drizzle porque lo
 * ejecutan dos procesos distintos: el endpoint interno de la Mini App -cuando
 * el bot reenvía un `successful_payment`- y el reconciliador del worker
 * -cuando ese reenvío se perdió-. Aplicar un pago son tres escrituras
 * acopladas (asiento, suscripción y plan del usuario) que deben ir en una sola
 * transacción; dos implementaciones divergirían, y su modo de divergencia es
 * exactamente "el usuario pagó y no tiene premium".
 */

export type PaymentIngestSource = "bot_forward" | "reconcile" | "manual";

export type StarPaymentInput = {
  userId: number;
  chargeId: string;
  starsAmount: number;
  invoicePayload: string | null;
  isRecurring: boolean;
  isFirstRecurring: boolean;
  /** Epoch en segundos que envía Telegram, o nulo si no viene. */
  subscriptionExpirationDate: number | null;
  raw?: Record<string, unknown>;
};

export type ApplyOutcome =
  | { applied: true; periodEnd: Date }
  /** Ya estaba asentado: reenvío repetido o reconciliación de algo ya aplicado. */
  | { applied: false; reason: "duplicate" };

const THIRTY_DAYS_MS = 2_592_000_000;

function periodEndFrom(input: StarPaymentInput): Date {
  // Telegram manda la caducidad real de la suscripción. Cuando no viene -por
  // ejemplo en un cargo suelto- se asume el periodo estándar, que es el único
  // que este producto vende.
  return input.subscriptionExpirationDate
    ? new Date(input.subscriptionExpirationDate * 1000)
    : new Date(Date.now() + THIRTY_DAYS_MS);
}

/**
 * Asienta un cargo y deja al usuario en premium hasta el fin del periodo.
 *
 * Idempotente por construcción: el `INSERT` en el libro mayor es la **puerta**.
 * Si entra en conflicto por `telegram_payment_charge_id`, la función sale sin
 * tocar nada más, y por eso el reenvío del bot, su reintento y el
 * reconciliador pueden competir por el mismo cargo sin pisarse.
 */
export async function applyStarPayment(
  sql: Sql,
  input: StarPaymentInput,
  source: PaymentIngestSource,
): Promise<ApplyOutcome> {
  const periodEnd = periodEndFrom(input);
  return await sql.begin(async (tx) => {
    const inserted = await tx`
      INSERT INTO "star_payments" (
        "telegram_payment_charge_id", "user_id", "kind", "stars_amount",
        "invoice_payload", "is_recurring", "is_first_recurring",
        "subscription_expiration", "ingest_source", "raw_payload"
      ) VALUES (
        ${input.chargeId}, ${input.userId}, 'charge', ${input.starsAmount},
        ${input.invoicePayload}, ${input.isRecurring}, ${input.isFirstRecurring},
        ${periodEnd}, ${source}, ${JSON.stringify(input.raw ?? {})}::jsonb
      )
      ON CONFLICT ("telegram_payment_charge_id") DO NOTHING
      RETURNING "telegram_payment_charge_id"
    `;
    if (inserted.length === 0) {
      return { applied: false, reason: "duplicate" } as const;
    }

    // `GREATEST` en las dos fechas: si el reconciliador recoge el cargo N+1
    // antes de que el bot entregue el N, aplicar el viejo después no puede
    // acortar una suscripción ya extendida.
    await tx`
      INSERT INTO "star_subscriptions" (
        "user_id", "latest_charge_id", "first_charge_id", "stars_amount",
        "current_period_end", "status"
      ) VALUES (
        ${input.userId}, ${input.chargeId}, ${input.chargeId},
        ${input.starsAmount}, ${periodEnd}, 'active'
      )
      ON CONFLICT ("user_id") DO UPDATE
         SET "latest_charge_id" = EXCLUDED."latest_charge_id",
             "stars_amount" = EXCLUDED."stars_amount",
             "current_period_end" = GREATEST(
               "star_subscriptions"."current_period_end",
               EXCLUDED."current_period_end"),
             "charge_count" = "star_subscriptions"."charge_count" + 1,
             "status" = 'active',
             "cancel_requested_at" = NULL,
             "updated_at" = now()
    `;

    await tx`
      UPDATE "miniapp_users"
         SET "plan" = 'premium',
             "plan_source" = 'stars',
             "plan_expires_at" = GREATEST(
               COALESCE("plan_expires_at", now()), ${periodEnd}::timestamptz),
             "plan_updated_at" = now()
       WHERE "telegram_user_id" = ${input.userId}
    `;

    if (input.invoicePayload) {
      await tx`
        UPDATE "star_invoices" SET "consumed_at" = now()
         WHERE "user_id" = ${input.userId} AND "consumed_at" IS NULL
           AND ${input.invoicePayload} LIKE '%' || "nonce" || '%'
      `;
    }
    return { applied: true, periodEnd } as const;
  });
}

export type StarRefundInput = {
  userId: number;
  chargeId: string;
  starsAmount: number;
  raw?: Record<string, unknown>;
};

/**
 * Revoca el acceso tras un reembolso.
 *
 * Misma forma con puerta en el libro mayor. Hace falta manejarlo también para
 * reembolsos iniciados desde el propio Telegram: sin esto, alguien a quien se
 * le devolvió el dinero conservaría premium hasta fin de periodo.
 */
export async function applyStarRefund(
  sql: Sql,
  input: StarRefundInput,
  source: PaymentIngestSource,
): Promise<ApplyOutcome> {
  const now = new Date();
  return await sql.begin(async (tx) => {
    const inserted = await tx`
      INSERT INTO "star_payments" (
        "telegram_payment_charge_id", "user_id", "kind", "stars_amount",
        "ingest_source", "raw_payload"
      ) VALUES (
        ${`refund:${input.chargeId}`}, ${input.userId}, 'refund',
        ${input.starsAmount}, ${source}, ${JSON.stringify(input.raw ?? {})}::jsonb
      )
      ON CONFLICT ("telegram_payment_charge_id") DO NOTHING
      RETURNING "telegram_payment_charge_id"
    `;
    if (inserted.length === 0) {
      return { applied: false, reason: "duplicate" } as const;
    }

    await tx`
      UPDATE "star_subscriptions"
         SET "status" = 'refunded', "updated_at" = now()
       WHERE "user_id" = ${input.userId}
    `;
    // No se toca a un administrador: su premium no viene de un cobro y
    // devolverle el dinero de otra cosa no puede dejarlo fuera del panel.
    await tx`
      UPDATE "miniapp_users"
         SET "plan" = 'free',
             "plan_source" = 'refunded',
             "plan_expires_at" = now(),
             "plan_updated_at" = now()
       WHERE "telegram_user_id" = ${input.userId}
         AND "plan_source" <> 'admin'
    `;
    return { applied: true, periodEnd: now } as const;
  });
}

/**
 * Marca como vencido lo que ya pasó de fecha.
 *
 * Es sólo pulcritud para el panel de administración: la corrección nunca
 * depende de este barrido porque `effective_plan` calcula la caducidad al leer.
 * Un barrido que no corra no puede, por tanto, dejar premium a quien no paga.
 */
export async function sweepExpiredPlans(sql: Sql): Promise<number> {
  const rows = await sql`
    UPDATE "miniapp_users"
       SET "plan" = 'free', "plan_updated_at" = now()
     WHERE "plan" = 'premium'
       AND "plan_source" <> 'admin'
       AND "plan_expires_at" <= now()
    RETURNING "telegram_user_id"
  `;
  await sql`
    UPDATE "star_subscriptions"
       SET "status" = 'expired', "updated_at" = now()
     WHERE "status" IN ('active', 'canceled')
       AND "current_period_end" <= now()
  `;
  return rows.length;
}
