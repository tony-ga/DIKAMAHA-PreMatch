import { NextRequest, NextResponse } from "next/server";

import { invalidateEntitlement } from "@/lib/auth/entitlements";
import { getSubscription, subscriptionPeriodEnd, verifyStripeSignature } from "@/lib/billing/stripe";
import {
  applyStripeSubscription,
  applyStripeTermination,
  userIdForCustomer,
} from "@/lib/billing/stripe-apply";
import { rawDatabase } from "@/lib/db";
import { env, stripeEnabled } from "@/lib/env";
import { jsonError } from "@/lib/http";

type StripeEvent = {
  id?: string;
  type?: string;
  data?: { object?: Record<string, unknown> };
};

/**
 * Confirmaciones de pago de Stripe.
 *
 * Es la única ruta del servicio sin sesión ni CSRF, y no es una excepción a la
 * regla sino la regla aplicada a otro emisor: quien llama no es un navegador
 * con cookie sino Stripe con una firma HMAC sobre el cuerpo. Por eso el cuerpo
 * se lee **crudo** con `text()` y nunca con `json()`: reserializar el JSON
 * cambia un espacio y la firma deja de cuadrar.
 *
 * Devuelve 200 en cuanto el evento queda asentado o resulta duplicado. Stripe
 * reintenta hasta recibir un 2xx, así que un error real aquí se traduce en
 * reintentos, que es justo lo que se quiere: la tabla `stripe_events` hace que
 * repetir sea inofensivo.
 */
export async function POST(request: NextRequest) {
  if (!stripeEnabled()) return jsonError("stripe_disabled", 503);
  const rawBody = await request.text();
  const signature = request.headers.get("stripe-signature");
  if (!verifyStripeSignature(rawBody, signature, env().STRIPE_WEBHOOK_SECRET)) {
    return jsonError("stripe_signature_invalid", 400);
  }

  let event: StripeEvent;
  try {
    event = JSON.parse(rawBody) as StripeEvent;
  } catch {
    return jsonError("stripe_payload_invalid", 400);
  }
  const eventId = typeof event.id === "string" ? event.id : "";
  const eventType = typeof event.type === "string" ? event.type : "";
  const object = event.data?.object ?? {};
  if (!eventId || !eventType) return jsonError("stripe_payload_invalid", 400);

  const sql = rawDatabase();
  try {
    switch (eventType) {
      case "checkout.session.completed":
      case "invoice.paid": {
        const subscriptionId = readSubscriptionId(object);
        if (!subscriptionId) break;
        const subscription = await getSubscription(subscriptionId);
        const periodEnd = subscriptionPeriodEnd(subscription);
        if (!periodEnd) break;
        const userId = await resolveUser(sql, object, subscription.metadata, subscription.customer);
        if (!userId) break;
        const outcome = await applyStripeSubscription(sql, {
          userId,
          eventId,
          eventType,
          subscriptionId,
          customerId: subscription.customer,
          priceId: subscription.items?.data?.[0]?.price?.id ?? env().STRIPE_PRICE_ID,
          currentPeriodEnd: periodEnd,
          raw: object,
        });
        // Sin esto el alta tardaría hasta 60 s en notarse -el TTL de la caché
        // de titularidad-, y el usuario volvería del checkout a una pantalla
        // que todavía le pide pagar.
        if (outcome.applied) invalidateEntitlement(userId);
        break;
      }
      case "customer.subscription.deleted":
      case "charge.refunded": {
        const customerId = typeof object.customer === "string" ? object.customer : null;
        const userId = await resolveUser(sql, object, readMetadata(object), customerId);
        if (!userId) break;
        const outcome = await applyStripeTermination(sql, {
          userId,
          eventId,
          eventType,
          status: eventType === "charge.refunded" ? "refunded" : "canceled",
          raw: object,
        });
        if (outcome.applied) invalidateEntitlement(userId);
        break;
      }
      default:
        // Stripe entrega todo lo que esté suscrito en el endpoint. Lo que no se
        // maneja se acepta en silencio: devolver error haría que Stripe
        // reintentara indefinidamente un evento que nunca se va a aplicar.
        break;
    }
  } catch (error) {
    console.error(JSON.stringify({
      event: "stripe_webhook_failed",
      eventId,
      eventType,
      error: error instanceof Error ? error.message : "unknown",
    }));
    // 500 a propósito: Stripe reintentará, y `stripe_events` garantiza que el
    // reintento no puede aplicar dos veces lo que ya se aplicó.
    return jsonError("stripe_webhook_failed", 500);
  }
  return NextResponse.json({ received: true });
}

function readSubscriptionId(object: Record<string, unknown>): string | null {
  if (typeof object.subscription === "string") return object.subscription;
  // Una factura de suscripción referencia la suscripción en su primera línea
  // cuando el campo de nivel superior no viene.
  const parent = object.parent as { subscription_details?: { subscription?: unknown } } | undefined;
  const nested = parent?.subscription_details?.subscription;
  if (typeof nested === "string") return nested;
  if (typeof object.id === "string" && object.object === "subscription") return object.id;
  return null;
}

function readMetadata(object: Record<string, unknown>): Record<string, string> | undefined {
  const metadata = object.metadata;
  return metadata && typeof metadata === "object"
    ? metadata as Record<string, string>
    : undefined;
}

/**
 * De quién es este pago.
 *
 * Tres vías en orden de fiabilidad, porque no todos los eventos traen lo
 * mismo: el `client_reference_id` que se fijó al abrir el checkout, los
 * metadatos de la suscripción, y por último la tabla de clientes -que es la
 * única disponible en una renovación que llega meses después-.
 */
async function resolveUser(
  sql: ReturnType<typeof rawDatabase>,
  object: Record<string, unknown>,
  metadata: Record<string, string> | undefined,
  customerId: string | null,
): Promise<number | null> {
  const reference = Number(object.client_reference_id);
  if (Number.isSafeInteger(reference) && reference > 0) return reference;
  const fromMetadata = Number(metadata?.telegram_user_id);
  if (Number.isSafeInteger(fromMetadata) && fromMetadata > 0) return fromMetadata;
  if (!customerId) return null;
  return await userIdForCustomer(sql, customerId);
}
