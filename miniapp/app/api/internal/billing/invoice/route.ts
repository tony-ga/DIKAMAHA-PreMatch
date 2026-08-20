import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { subscriptionBlock } from "@/lib/billing/gateways";
import { issueInvoice } from "@/lib/billing/invoice";
import { rawDatabase } from "@/lib/db";
import { TelegramApiError } from "@/lib/billing/telegram";
import { authError, jsonError, requireInternalKey } from "@/lib/http";

const schema = z.object({
  user_id: z.number().int().positive(),
});

/**
 * Emite una factura a petición del bot, para su comando `/premium`.
 *
 * El bot podría llamar a `createInvoiceLink` él mismo -tiene el token-, pero
 * entonces habría dos sitios decidiendo el precio y firmando payloads. Con esta
 * ruta, el precio vigente y el asiento en `star_invoices` salen del mismo
 * código que usa la Mini App.
 */
export async function POST(request: NextRequest) {
  try {
    requireInternalKey(request);
    const parsed = schema.safeParse(await request.json());
    if (!parsed.success) return jsonError("invoice_request_invalid", 422);
    // El invariante de DEC-220 se comprueba también aquí. Esta puerta no
    // comprobaba **nada**: era la única por la que se podía abrir una segunda
    // suscripción incluso sobre una Stars ya viva. El bot degrada cualquier 4xx
    // a "no se pudo abrir el pago", que es un mensaje pobre pero infinitamente
    // preferible a cobrar dos veces.
    const blocked = await subscriptionBlock(rawDatabase(), parsed.data.user_id, "stars");
    if (blocked) return jsonError(blocked, 409);
    const invoice = await issueInvoice(parsed.data.user_id, "bot");
    return NextResponse.json(invoice);
  } catch (error) {
    if (error instanceof TelegramApiError) {
      console.error(JSON.stringify({
        event: "billing_invoice_failed", origin: "bot", detail: error.description,
      }));
      return jsonError("billing_invoice_unavailable", 503);
    }
    return authError(error);
  }
}
