import { NextRequest, NextResponse } from "next/server";

import { subscriptionBlock } from "@/lib/billing/gateways";
import { issueInvoice } from "@/lib/billing/invoice";
import { TelegramApiError } from "@/lib/billing/telegram";
import { rawDatabase } from "@/lib/db";
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
    // Mismo guardián que el checkout de Stripe, y por la misma razón: esto
    // comprobaba sólo si ya había una suscripción **Stars**, así que quien
    // pagaba con tarjeta podía comprar Stars encima y pagar dos veces el mismo
    // mes. Sólo bloquea a quien tiene un cobro recurrente vivo: un
    // administrador o alguien con acceso heredado sí puede suscribirse, porque
    // su premium no viene de ninguna pasarela y querrá continuidad al terminar.
    const blocked = await subscriptionBlock(rawDatabase(), session.userId, "stars");
    if (blocked) return jsonError(blocked, 409);
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
