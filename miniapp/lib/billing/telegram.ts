import { env } from "@/lib/env";

/**
 * Cliente mínimo de la Bot API para lo que exige el cobro con Stars.
 *
 * La Mini App puede llamar a Telegram directamente porque ya custodia
 * `TELEGRAM_BOT_TOKEN` para validar `initData`. El bot, en cambio, **no** emite
 * facturas por su cuenta: pide el enlace aquí, para que el precio, el payload
 * firmado y la fila de `star_invoices` los produzca un único camino de código.
 */
export class TelegramApiError extends Error {
  constructor(readonly method: string, readonly description: string) {
    super(`telegram_${method}_failed`);
  }
}

async function call<T>(method: string, body: Record<string, unknown>): Promise<T> {
  const response = await fetch(
    `https://api.telegram.org/bot${env().TELEGRAM_BOT_TOKEN}/${method}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15_000),
    },
  );
  const payload = await response.json() as {
    ok: boolean; result?: T; description?: string;
  };
  if (!response.ok || !payload.ok) {
    throw new TelegramApiError(method, payload.description ?? `http_${response.status}`);
  }
  return payload.result as T;
}

/** Telegram sólo admite este periodo para suscripciones Stars. */
export const STAR_SUBSCRIPTION_PERIOD_SECONDS = 2_592_000;

export type InvoiceInput = {
  payload: string;
  starsAmount: number;
  title: string;
  description: string;
  label: string;
};

/**
 * Crea el enlace de pago recurrente.
 *
 * Restricciones que impone Telegram y que este cuerpo respeta: para `XTR` no se
 * envía `provider_token`, `prices` lleva exactamente un elemento, el importe es
 * un número entero de Stars -sin unidades menores, a diferencia del dinero
 * fiduciario- y `subscription_period` debe ser exactamente 2592000.
 */
export function createInvoiceLink(input: InvoiceInput): Promise<string> {
  return call<string>("createInvoiceLink", {
    title: input.title,
    description: input.description,
    payload: input.payload,
    currency: "XTR",
    prices: [{ label: input.label, amount: input.starsAmount }],
    subscription_period: STAR_SUBSCRIPTION_PERIOD_SECONDS,
  });
}

/** Cancela o reactiva la renovación de una suscripción ya cobrada. */
export function editUserStarSubscription(
  userId: number, chargeId: string, isCanceled: boolean,
): Promise<boolean> {
  return call<boolean>("editUserStarSubscription", {
    user_id: userId,
    telegram_payment_charge_id: chargeId,
    is_canceled: isCanceled,
  });
}

export function refundStarPayment(
  userId: number, chargeId: string,
): Promise<boolean> {
  return call<boolean>("refundStarPayment", {
    user_id: userId,
    telegram_payment_charge_id: chargeId,
  });
}

export type StarTransaction = {
  id: string;
  amount: number;
  date: number;
  source?: { type: string; user?: { id: number }; invoice_payload?: string };
  receiver?: { type: string; user?: { id: number }; invoice_payload?: string };
};

/**
 * Verdad de Telegram sobre los cargos del bot.
 *
 * Es la red de seguridad: si el reenvío del bot a la Mini App se pierde, un
 * usuario que pagó se quedaría sin producto en silencio, que es el peor
 * desenlace posible de esta función.
 */
export function getStarTransactions(
  offset = 0, limit = 100, token = env().TELEGRAM_BOT_TOKEN,
): Promise<{ transactions: StarTransaction[] }> {
  void token;
  return call<{ transactions: StarTransaction[] }>("getStarTransactions", {
    offset, limit,
  });
}
