import { sql } from "drizzle-orm";

import { database } from "@/lib/db";
import { newNonce, signBillingPayload } from "@/lib/billing/payload";
import { activePlan } from "@/lib/billing/plans";
import { createInvoiceLink } from "@/lib/billing/telegram";

export type InvoiceOrigin = "miniapp" | "bot";

/**
 * Emite un enlace de pago para un usuario.
 *
 * Camino único a propósito. El bot **no** llama a `createInvoiceLink` por su
 * cuenta: pide el enlace aquí, para que el precio vigente, la firma del payload
 * y el asiento en `star_invoices` los produzca una sola pieza de código. Dos
 * emisores divergirían justo en el dato que después no se puede auditar.
 */
export async function issueInvoice(
  userId: number, origin: InvoiceOrigin,
): Promise<{ link: string; starsAmount: number }> {
  const plan = await activePlan();
  const nonce = newNonce();
  const payload = signBillingPayload({
    userId, planCode: plan.code, starsAmount: plan.starsAmount, nonce,
  });

  await database().execute(sql`
    INSERT INTO "star_invoices" ("nonce", "user_id", "plan_code", "stars_amount", "origin")
    VALUES (${nonce}, ${userId}, ${plan.code}, ${plan.starsAmount}, ${origin})
    ON CONFLICT ("nonce") DO NOTHING
  `);

  const link = await createInvoiceLink({
    payload,
    starsAmount: plan.starsAmount,
    title: "DIKAMAHA Premium",
    // Se vende acceso y volumen. Ni rentabilidad, ni aciertos garantizados,
    // ni retorno: el proyecto tiene congelados ROI, Kelly y stakes, y la
    // superficie de venta es justo donde esa restricción más importa.
    description:
      "Acceso mensual: predicciones sin límite, análisis en vivo y el menú de "
      + "mayor probabilidad. Renovación automática cada 30 días, cancelable "
      + "en cualquier momento desde Telegram.",
    label: "DIKAMAHA Premium · 30 días",
  });
  return { link, starsAmount: plan.starsAmount };
}
