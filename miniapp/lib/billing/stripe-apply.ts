import type { Sql } from "postgres";

/**
 * Aplicación de suscripciones Stripe.
 *
 * Hermano de `lib/billing/apply.ts` y escrito con la misma forma por la misma
 * razón: la puerta es el `INSERT` en el libro mayor. Stripe **reintenta** los
 * webhooks hasta recibir un 2xx y puede entregar el mismo evento más de una
 * vez sin que nada haya fallado, así que la idempotencia no puede depender del
 * cuidado del manejador.
 *
 * Lo que este módulo deliberadamente **no** hace es inventar una segunda
 * noción de titularidad: escribe `plan`, `plan_source` y `plan_expires_at`
 * sobre `miniapp_users` exactamente igual que el camino de Stars, de modo que
 * `resolveEntitlement` y `effective_plan()` siguen siendo la única autoridad.
 */

export type StripeSubscriptionInput = {
  userId: number;
  eventId: string;
  eventType: string;
  subscriptionId: string;
  customerId: string;
  priceId: string;
  /** Epoch en segundos del fin de periodo que manda Stripe. */
  currentPeriodEnd: number;
  raw?: Record<string, unknown>;
};

export type StripeApplyOutcome =
  | { applied: true; periodEnd: Date }
  | { applied: false; reason: "duplicate" };

/**
 * Asienta un cobro y deja al usuario en premium hasta el fin del periodo.
 *
 * Sirve tanto al alta (`checkout.session.completed`) como a cada renovación
 * (`invoice.paid`): los dos significan lo mismo -hay dinero y hay periodo- y
 * distinguirlos sólo produciría dos caminos que pueden divergir.
 */
export async function applyStripeSubscription(
  sql: Sql,
  input: StripeSubscriptionInput,
): Promise<StripeApplyOutcome> {
  const periodEnd = new Date(input.currentPeriodEnd * 1000);
  return await sql.begin(async (tx) => {
    const inserted = await tx`
      INSERT INTO "stripe_events" ("event_id", "user_id", "event_type", "raw_payload")
      VALUES (${input.eventId}, ${input.userId}, ${input.eventType},
              ${JSON.stringify(input.raw ?? {})}::jsonb)
      ON CONFLICT ("event_id") DO NOTHING
      RETURNING "event_id"
    `;
    if (inserted.length === 0) return { applied: false, reason: "duplicate" } as const;

    await tx`
      INSERT INTO "stripe_customers" ("user_id", "stripe_customer_id")
      VALUES (${input.userId}, ${input.customerId})
      ON CONFLICT ("user_id") DO UPDATE
         SET "stripe_customer_id" = EXCLUDED."stripe_customer_id"
    `;

    await tx`
      INSERT INTO "stripe_subscriptions" (
        "user_id", "stripe_subscription_id", "stripe_customer_id", "price_id",
        "current_period_end", "status"
      ) VALUES (
        ${input.userId}, ${input.subscriptionId}, ${input.customerId},
        ${input.priceId}, ${periodEnd}, 'active'
      )
      ON CONFLICT ("user_id") DO UPDATE
         SET "stripe_subscription_id" = EXCLUDED."stripe_subscription_id",
             "stripe_customer_id" = EXCLUDED."stripe_customer_id",
             "price_id" = EXCLUDED."price_id",
             -- GREATEST por lo mismo que en Stars: si dos eventos llegan
             -- desordenados, aplicar el viejo después no puede acortar una
             -- suscripción ya extendida.
             "current_period_end" = GREATEST(
               "stripe_subscriptions"."current_period_end",
               EXCLUDED."current_period_end"),
             "charge_count" = "stripe_subscriptions"."charge_count" + 1,
             "status" = 'active',
             "cancel_requested_at" = NULL,
             "updated_at" = now()
    `;

    await tx`
      UPDATE "miniapp_users"
         SET "plan" = 'premium',
             "plan_source" = 'stripe',
             "plan_expires_at" = GREATEST(
               COALESCE("plan_expires_at", now()), ${periodEnd}::timestamptz),
             "plan_updated_at" = now()
       WHERE "telegram_user_id" = ${input.userId}
    `;
    return { applied: true, periodEnd } as const;
  });
}

export type StripeTerminationInput = {
  userId: number;
  eventId: string;
  eventType: string;
  /** `canceled` al terminar la suscripción, `refunded` al devolver el dinero. */
  status: "canceled" | "refunded";
  raw?: Record<string, unknown>;
};

/**
 * Cierra la suscripción y retira el producto.
 *
 * Un reembolso corta de inmediato -quien recuperó su dinero no conserva el
 * mes-, mientras que una cancelación deja correr el periodo ya pagado: Stripe
 * emite `customer.subscription.deleted` cuando el periodo termina, no cuando el
 * usuario pulsa cancelar, así que llegar aquí ya significa que se acabó.
 */
export async function applyStripeTermination(
  sql: Sql,
  input: StripeTerminationInput,
): Promise<StripeApplyOutcome> {
  const now = new Date();
  return await sql.begin(async (tx) => {
    const inserted = await tx`
      INSERT INTO "stripe_events" ("event_id", "user_id", "event_type", "raw_payload")
      VALUES (${input.eventId}, ${input.userId}, ${input.eventType},
              ${JSON.stringify(input.raw ?? {})}::jsonb)
      ON CONFLICT ("event_id") DO NOTHING
      RETURNING "event_id"
    `;
    if (inserted.length === 0) return { applied: false, reason: "duplicate" } as const;

    await tx`
      UPDATE "stripe_subscriptions"
         SET "status" = ${input.status}, "updated_at" = now()
       WHERE "user_id" = ${input.userId}
    `;
    // La guarda `plan_source = 'stripe'` hace dos cosas a la vez: con dos
    // pasarelas, un evento de una no puede retirar el producto pagado por la
    // otra -aunque DEC-220 impide que coexistan, el orden de llegada de los
    // eventos no está garantizado-, y de paso deja fuera a los administradores,
    // cuyo premium no viene de ningún cobro.
    await tx`
      UPDATE "miniapp_users"
         SET "plan" = 'free',
             "plan_source" = ${input.status === "refunded" ? "refunded" : "default"},
             "plan_expires_at" = now(),
             "plan_updated_at" = now()
       WHERE "telegram_user_id" = ${input.userId}
         AND "plan_source" = 'stripe'
    `;
    return { applied: true, periodEnd: now } as const;
  });
}

/**
 * Usuario dueño de un cliente de Stripe.
 *
 * La necesitan las renovaciones: `invoice.paid` de un cobro recurrente llega
 * meses después del alta y puede no traer más identificación que el cliente.
 */
export async function userIdForCustomer(
  sql: Sql,
  customerId: string,
): Promise<number | null> {
  const [row] = await sql`
    SELECT "user_id" FROM "stripe_customers"
     WHERE "stripe_customer_id" = ${customerId} LIMIT 1
  `;
  return row ? Number(row.user_id) : null;
}

export type GatewayStatus = {
  stars: boolean;
  stripe: boolean;
  stripeCustomerId: string | null;
};

/**
 * Qué suscripción viva tiene ya el usuario.
 *
 * Es la comprobación de DEC-220. `plan_expires_at` es **una sola** fecha por
 * usuario, así que dos pasarelas activas escribirían sobre el mismo valor y el
 * usuario podría pagar dos veces el mismo mes sin que el sistema lo notara. Se
 * mira antes de abrir un checkout, que es el único momento en que todavía se
 * puede evitar.
 */
export async function gatewayStatus(sql: Sql, userId: number): Promise<GatewayStatus> {
  const [stars] = await sql`
    SELECT 1 FROM "star_subscriptions"
     WHERE "user_id" = ${userId} AND "status" = 'active'
       AND "current_period_end" > now()
     LIMIT 1
  `;
  const [stripe] = await sql`
    SELECT "stripe_customer_id" FROM "stripe_subscriptions"
     WHERE "user_id" = ${userId} AND "status" = 'active'
       AND "current_period_end" > now()
     LIMIT 1
  `;
  const [customer] = await sql`
    SELECT "stripe_customer_id" FROM "stripe_customers"
     WHERE "user_id" = ${userId} LIMIT 1
  `;
  return {
    stars: Boolean(stars),
    stripe: Boolean(stripe),
    stripeCustomerId: (customer?.stripe_customer_id as string | undefined) ?? null,
  };
}
