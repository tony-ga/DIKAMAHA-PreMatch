import { createHmac, timingSafeEqual } from "node:crypto";

import { env } from "@/lib/env";

/**
 * Cliente mínimo de Stripe sobre `fetch`.
 *
 * Sin el SDK oficial a propósito. Fase 108 dejó el runtime deliberadamente
 * pequeño y lo que se necesita aquí son tres llamadas -crear cliente, crear
 * sesión de checkout, crear sesión del portal- más la verificación de una
 * firma HMAC que `node:crypto` ya sabe hacer. Es el mismo criterio con el que
 * `lib/dikamaha.ts` habla con la API de Python: `fetch` y un timeout.
 *
 * La API de Stripe es `application/x-www-form-urlencoded`, no JSON, incluso
 * para los parámetros anidados: `subscription_data[metadata][user_id]`.
 */

const API = "https://api.stripe.com/v1";
const TIMEOUT_MS = 20_000;

function formEncode(payload: Record<string, string | number | undefined>): string {
  const body = new URLSearchParams();
  for (const [key, value] of Object.entries(payload)) {
    if (value === undefined) continue;
    body.set(key, String(value));
  }
  return body.toString();
}

async function stripeRequest<T>(
  path: string,
  payload: Record<string, string | number | undefined>,
  idempotencyKey?: string,
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${API}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env().STRIPE_SECRET_KEY}`,
        "Content-Type": "application/x-www-form-urlencoded",
        // Stripe deduplica por esta cabecera durante 24 h: sin ella, un
        // reintento del usuario -o del navegador- abre una segunda sesión de
        // checkout por el mismo concepto.
        ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
      },
      body: formEncode(payload),
      signal: controller.signal,
    });
    if (!response.ok) {
      // El cuerpo de error de Stripe lleva el mensaje real; se registra pero no
      // se propaga al cliente, que no debe aprender nada de la configuración.
      const detail = await response.text().catch(() => "");
      console.error(JSON.stringify({
        event: "stripe_request_failed", path, status: response.status, detail: detail.slice(0, 500),
      }));
      throw new Error("stripe_request_failed");
    }
    return await response.json() as T;
  } finally {
    clearTimeout(timer);
  }
}

async function stripeGet<T>(path: string): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(`${API}${path}`, {
      headers: { Authorization: `Bearer ${env().STRIPE_SECRET_KEY}` },
      signal: controller.signal,
    });
    if (!response.ok) {
      console.error(JSON.stringify({
        event: "stripe_request_failed", path, status: response.status,
      }));
      throw new Error("stripe_request_failed");
    }
    return await response.json() as T;
  } finally {
    clearTimeout(timer);
  }
}

export type StripeSubscription = {
  id: string;
  customer: string;
  status: string;
  current_period_end: number;
  items?: { data?: Array<{ price?: { id?: string }; current_period_end?: number }> };
  metadata?: Record<string, string>;
};

/**
 * Lee la suscripción para saber hasta cuándo está pagada.
 *
 * El webhook no puede deducirlo del evento: `checkout.session.completed` no
 * trae el periodo, e `invoice.paid` lo trae en una forma que ha cambiado entre
 * versiones de la API. Preguntar por el estado actual en vez de reconstruirlo
 * del evento es además lo correcto ante entregas desordenadas: lo que se
 * escribe es lo que Stripe dice ahora, no lo que decía cuando se emitió el
 * evento.
 */
export async function getSubscription(id: string): Promise<StripeSubscription> {
  return await stripeGet<StripeSubscription>(`/subscriptions/${encodeURIComponent(id)}`);
}

/**
 * Si el dinero ya llegó.
 *
 * Una sesión de checkout puede **completarse sin haber cobrado**: con métodos
 * de pago diferidos -OXXO o SPEI, entre otros- el pago queda pendiente y el
 * importe llega días después. Conceder ahí sería entregar el producto antes de
 * cobrarlo. `invoice.paid`, en cambio, sólo se emite con el pago hecho, así que
 * no necesita comprobación.
 *
 * `no_payment_required` es legítimo: es lo que devuelve una suscripción que
 * empieza con periodo de prueba.
 */
export function paymentSettled(
  eventType: string,
  object: Record<string, unknown>,
): boolean {
  if (eventType !== "checkout.session.completed") return true;
  return object.payment_status === "paid"
    || object.payment_status === "no_payment_required";
}

/** Precio de la suscripción, o `null` si el evento no trae ninguno. */
export function subscriptionPriceId(subscription: StripeSubscription): string | null {
  return subscription.items?.data?.[0]?.price?.id ?? null;
}

/** Fin de periodo, que según la versión de la API vive en la suscripción o en su ítem. */
export function subscriptionPeriodEnd(subscription: StripeSubscription): number {
  const fromItem = subscription.items?.data?.[0]?.current_period_end;
  return subscription.current_period_end ?? fromItem ?? 0;
}

export type StripeCheckoutSession = { id: string; url: string; customer?: string };

export async function createCustomer(userId: number, username?: string): Promise<string> {
  const customer = await stripeRequest<{ id: string }>("/customers", {
    "metadata[telegram_user_id]": userId,
    ...(username ? { "metadata[telegram_username]": username } : {}),
  }, `customer:${userId}`);
  return customer.id;
}

export async function createCheckoutSession(input: {
  userId: number;
  customerId: string;
  priceId: string;
  successUrl: string;
  cancelUrl: string;
}): Promise<StripeCheckoutSession> {
  return await stripeRequest<StripeCheckoutSession>("/checkout/sessions", {
    mode: "subscription",
    customer: input.customerId,
    "line_items[0][price]": input.priceId,
    "line_items[0][quantity]": 1,
    success_url: input.successUrl,
    cancel_url: input.cancelUrl,
    // `client_reference_id` y los metadatos de la suscripción son las dos vías
    // por las que el webhook recupera de quién es el pago. Se ponen las dos
    // porque no todos los eventos traen la sesión: `invoice.paid` de un cobro
    // recurrente llega meses después y sólo conoce la suscripción.
    client_reference_id: String(input.userId),
    "subscription_data[metadata][telegram_user_id]": input.userId,
  });
}

export async function createPortalSession(
  customerId: string,
  returnUrl: string,
): Promise<{ url: string }> {
  return await stripeRequest<{ url: string }>("/billing_portal/sessions", {
    customer: customerId,
    return_url: returnUrl,
  });
}

/**
 * Verifica la firma de un webhook de Stripe.
 *
 * La cabecera tiene la forma `t=<epoch>,v1=<hex>[,v1=<hex>]`, y lo firmado es
 * `${t}.${cuerpo crudo}`. Dos detalles que no son opcionales:
 *
 * 1. El cuerpo tiene que ser el **texto exacto** recibido. Reserializar el
 *    JSON cambia un espacio y la firma deja de cuadrar.
 * 2. La tolerancia temporal es lo que impide reproducir un evento legítimo
 *    capturado hace semanas.
 *
 * Puede haber varias `v1` durante una rotación de secreto: basta que una
 * coincida, y se comparan todas en tiempo constante.
 */
export function verifyStripeSignature(
  rawBody: string,
  header: string | null,
  secret: string,
  nowSeconds = Math.floor(Date.now() / 1000),
  toleranceSeconds = 300,
): boolean {
  if (!header || !secret) return false;
  let timestamp = "";
  const signatures: string[] = [];
  for (const part of header.split(",")) {
    const [key, value] = part.split("=", 2);
    if (key?.trim() === "t") timestamp = value?.trim() ?? "";
    if (key?.trim() === "v1" && value) signatures.push(value.trim());
  }
  const issued = Number(timestamp);
  if (!Number.isSafeInteger(issued) || signatures.length === 0) return false;
  if (Math.abs(nowSeconds - issued) > toleranceSeconds) return false;

  const expected = createHmac("sha256", secret)
    .update(`${timestamp}.${rawBody}`)
    .digest();
  return signatures.some((candidate) => {
    if (!/^[a-f0-9]{64}$/i.test(candidate)) return false;
    const supplied = Buffer.from(candidate, "hex");
    return supplied.length === expected.length && timingSafeEqual(supplied, expected);
  });
}
