import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { createCheckoutSession, createCustomer } from "@/lib/billing/stripe";
import { gatewayStatus, subscriptionBlock } from "@/lib/billing/gateways";
import { rawDatabase } from "@/lib/db";
import { env, publicWebUrl, stripeEnabled } from "@/lib/env";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

/**
 * Abre el pago del sitio web.
 *
 * Devuelve la URL del checkout alojado de Stripe: ningún dato de tarjeta pasa
 * por DIKAMAHA. La confirmación **no** vuelve por aquí sino por el webhook, así
 * que la interfaz reconsulta la titularidad al volver en vez de dar por hecha
 * el alta -exactamente el mismo contrato que el pago con Stars-.
 */
export async function POST(request: NextRequest) {
  if (!stripeEnabled()) return jsonError("stripe_disabled", 503);
  try {
    const session = await authorizeRequest(request, true);
    const sql = rawDatabase();
    // DEC-220: una suscripción viva por usuario. `plan_expires_at` es una sola
    // fecha, así que dos pasarelas activas se pisarían y el usuario pagaría dos
    // veces el mismo mes. Se comprueba aquí porque es el último momento en que
    // todavía se puede evitar.
    const blocked = await subscriptionBlock(sql, session.userId, "stripe");
    if (blocked) return jsonError(blocked, 409);

    const gateways = await gatewayStatus(sql, session.userId);
    const customerId = gateways.stripeCustomerId
      ?? await createCustomer(session.userId, session.username);
    const origin = publicWebUrl();
    const checkout = await createCheckoutSession({
      userId: session.userId,
      customerId,
      priceId: env().STRIPE_PRICE_ID,
      successUrl: `${origin}/subscriptions?checkout=ok`,
      cancelUrl: `${origin}/subscriptions?checkout=cancel`,
    });
    return NextResponse.json({ url: checkout.url });
  } catch (error) {
    if (error instanceof Error && error.message === "stripe_request_failed") {
      return jsonError("billing_checkout_unavailable", 503);
    }
    return authError(error);
  }
}
