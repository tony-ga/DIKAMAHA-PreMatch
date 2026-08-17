import type { Sql } from "postgres";

import { applyStarPayment, applyStarRefund, sweepExpiredPlans } from "@/lib/billing/apply";
import { verifyBillingPayload } from "@/lib/billing/payload";

/**
 * Reconciliación de cobros Stars contra la verdad de Telegram.
 *
 * El camino primario de un pago es bot → endpoint interno de la Mini App, y
 * ese salto puede fallar: un despliegue en curso, un 502, un corte de red. Un
 * `successful_payment` perdido **deniega en silencio a alguien que pagó**, que
 * es el peor desenlace posible de esta función y el único que el usuario no
 * puede diagnosticar.
 *
 * `getStarTransactions` es lo que Telegram sabe de nuestros cargos, y
 * reprocesarlo es inofensivo porque el libro mayor está indexado por el id de
 * cargo: el reconciliador no necesita estado propio, ni cursor que persistir,
 * ni "último visto" que corromper.
 *
 * Vive dentro del worker de alertas y no en un servicio Railway nuevo: el
 * coste fijo es justamente lo que esta fase intenta amortizar, y el worker ya
 * tiene `DATABASE_URL` y el token del bot.
 *
 * No importa `lib/env`: el worker tiene su propia configuración y `env()`
 * exigiría variables que ese servicio no necesita. Por eso el secreto de firma
 * se pasa siempre explícito.
 */

export type ReconcileConfig = {
  botToken: string;
  billingSecret: string;
  /** Tope de páginas. Sólo se sigue paginando mientras haya reparaciones. */
  maxPages?: number;
};

export type ReconcileReport = {
  scanned: number;
  repaired: number;
  expired: number;
};

type StarTransaction = {
  id?: string;
  amount?: number;
  source?: { type?: string; user?: { id?: number }; invoice_payload?: string };
  receiver?: { type?: string; user?: { id?: number }; invoice_payload?: string };
};

async function fetchTransactions(
  token: string, offset: number, limit: number,
): Promise<StarTransaction[]> {
  const response = await fetch(
    `https://api.telegram.org/bot${token}/getStarTransactions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ offset, limit }),
      signal: AbortSignal.timeout(20_000),
    },
  );
  const payload = await response.json() as {
    ok?: boolean; result?: { transactions?: StarTransaction[] };
  };
  if (!response.ok || !payload.ok) throw new Error("get_star_transactions_failed");
  return payload.result?.transactions ?? [];
}

/**
 * Repara los cargos y reembolsos que no llegaron a asentarse.
 *
 * `repaired > 0` es una **condición de alerta**, no una estadística: significa
 * que el camino primario está perdiendo pagos y hay que investigarlo, no
 * taparlo con el reconciliador.
 */
export async function reconcileStarTransactions(
  sql: Sql, config: ReconcileConfig,
): Promise<ReconcileReport> {
  const maxPages = config.maxPages ?? 5;
  const pageSize = 100;
  let scanned = 0;
  let repaired = 0;

  for (let page = 0; page < maxPages; page += 1) {
    const transactions = await fetchTransactions(
      config.botToken, page * pageSize, pageSize);
    if (transactions.length === 0) break;
    scanned += transactions.length;
    let repairedInPage = 0;

    for (const transaction of transactions) {
      const chargeId = transaction.id;
      if (!chargeId) continue;
      // Cargo entrante: el dinero viene de un usuario. Reembolso: va hacia él.
      const incoming = transaction.source?.type === "user"
        ? transaction.source
        : null;
      const outgoing = transaction.receiver?.type === "user"
        ? transaction.receiver
        : null;
      const amount = Number(transaction.amount ?? 0);

      if (incoming?.user?.id) {
        // Una factura ajena o fabricada a mano no puede conceder un plan: se
        // exige la misma firma que en el camino primario.
        const payload = verifyBillingPayload(
          incoming.invoice_payload, config.billingSecret,
          { expectedUserId: incoming.user.id });
        if (!payload) continue;
        const outcome = await applyStarPayment(sql, {
          userId: incoming.user.id,
          chargeId,
          starsAmount: amount,
          invoicePayload: incoming.invoice_payload ?? null,
          isRecurring: true,
          isFirstRecurring: false,
          subscriptionExpirationDate: null,
          raw: transaction as unknown as Record<string, unknown>,
        }, "reconcile");
        if (outcome.applied) repairedInPage += 1;
        continue;
      }

      if (outgoing?.user?.id) {
        const outcome = await applyStarRefund(sql, {
          userId: outgoing.user.id,
          chargeId,
          starsAmount: amount,
          raw: transaction as unknown as Record<string, unknown>,
        }, "reconcile");
        if (outcome.applied) repairedInPage += 1;
      }
    }

    repaired += repairedInPage;
    // Sólo se profundiza cuando esta página reparó algo: "vamos por detrás" es
    // exactamente el caso en el que mirar más atrás está justificado.
    if (repairedInPage === 0) break;
  }

  const expired = await sweepExpiredPlans(sql);
  return { scanned, repaired, expired };
}
