import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { issueInvoice } from "@/lib/billing/invoice";
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
