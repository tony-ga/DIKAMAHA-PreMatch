import type { Sql } from "postgres";

/**
 * El invariante que sostiene las dos pasarelas: **una suscripción viva por
 * usuario** (DEC-220).
 *
 * Vive aparte de `stripe-apply.ts` porque no es un detalle de Stripe: mira las
 * dos pasarelas y lo consultan las tres puertas por las que alguien puede
 * empezar a pagar -el checkout de la web, la factura de la Mini App y la
 * factura que pide el bot-. Estaba dentro del módulo de Stripe y sólo lo
 * llamaba el checkout; el resultado era que el invariante se cumplía en una
 * dirección y no en la otra, y quien pagaba con tarjeta podía comprar Stars
 * encima y pagar dos veces el mismo mes.
 *
 * La comprobación es sobre la base, no sobre la titularidad: `plan_source` dice
 * de dónde vino el plan vigente, pero no si hay un cobro recurrente **vivo** al
 * otro lado. Un premium ya vencido no debe bloquear una compra nueva, y un
 * premium de administrador tampoco.
 */

export type Gateway = "stars" | "stripe";

export type GatewayStatus = {
  stars: boolean;
  stripe: boolean;
  stripeCustomerId: string | null;
};

export async function gatewayStatus(sql: Sql, userId: number): Promise<GatewayStatus> {
  const [stars] = await sql`
    SELECT 1 FROM "star_subscriptions"
     WHERE "user_id" = ${userId} AND "status" = 'active'
       AND "current_period_end" > now()
     LIMIT 1
  `;
  const [stripe] = await sql`
    SELECT 1 FROM "stripe_subscriptions"
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

/**
 * Código de error si el usuario no puede abrir una suscripción nueva.
 *
 * Distingue "ya tienes ésta" de "tienes la otra" a propósito: son dos
 * situaciones distintas para el usuario y sólo la segunda necesita explicarle
 * dónde está lo que ya paga. Devuelve `null` cuando puede seguir.
 */
export async function subscriptionBlock(
  sql: Sql,
  userId: number,
  requested: Gateway,
): Promise<"already_subscribed" | "stars_subscription_active" | "stripe_subscription_active" | null> {
  const status = await gatewayStatus(sql, userId);
  if (requested === "stars") {
    if (status.stars) return "already_subscribed";
    if (status.stripe) return "stripe_subscription_active";
    return null;
  }
  if (status.stripe) return "already_subscribed";
  if (status.stars) return "stars_subscription_active";
  return null;
}
