import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { issueInvoice } from "@/lib/billing/invoice";
import { TelegramApiError } from "@/lib/billing/telegram";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

/**
 * Enlace de pago para el usuario de la sesión.
 *
 * El cliente lo abre con `Telegram.WebApp.openInvoice`. La confirmación no
 * vuelve por aquí: llega al bot como `successful_payment` y éste la reenvía al
 * endpoint interno, así que la interfaz debe reconsultar la titularidad tras
 * un `paid` en lugar de dar por hecho el alta.
 */
export async function POST(request: NextRequest) {
  try {
    const session = await authorizeRequest(request, true);
    const entitlement = await resolveEntitlement(session.userId);
    if (entitlement.plan === "premium" && entitlement.planSource === "stars") {
      // Sólo bloquea a quien ya paga. Un administrador o alguien con acceso
      // heredado sí puede suscribirse: su premium tiene fecha o procedencia
      // distinta y querrá continuidad cuando termine.
      return jsonError("already_subscribed", 409);
    }
    const invoice = await issueInvoice(session.userId, "miniapp");
    return NextResponse.json(invoice);
  } catch (error) {
    if (error instanceof TelegramApiError) {
      console.error(JSON.stringify({
        event: "billing_invoice_failed", detail: error.description,
      }));
      return jsonError("billing_invoice_unavailable", 503);
    }
    return authError(error);
  }
}
