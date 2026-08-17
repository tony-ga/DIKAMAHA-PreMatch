import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { invalidateEntitlement } from "@/lib/auth/entitlements";
import { applyStarPayment } from "@/lib/billing/apply";
import { verifyBillingPayload } from "@/lib/billing/payload";
import { rawDatabase } from "@/lib/db";
import { authError, jsonError, requireInternalKey } from "@/lib/http";

const schema = z.object({
  user_id: z.number().int().positive(),
  telegram_payment_charge_id: z.string().min(1).max(200),
  invoice_payload: z.string().max(256).nullable().default(null),
  total_amount: z.number().int().nonnegative(),
  currency: z.string().max(10).default("XTR"),
  is_recurring: z.boolean().default(false),
  is_first_recurring: z.boolean().default(false),
  subscription_expiration_date: z.number().int().positive().nullable().default(null),
});

/**
 * Asienta un `successful_payment` que el bot recibió por `getUpdates`.
 *
 * Sólo el bot hace long polling, así que los pagos llegan a Python; sólo la
 * Mini App tiene base de datos. En vez de abrirle una conexión al bot -lo que
 * duplicaría en otro lenguaje una transacción de tres escrituras acopladas y
 * rompería la regla de la Fase 109-, el bot reenvía aquí con clave compartida.
 * El coste es un salto de red que puede fallar, y de eso se encarga la
 * reconciliación por `getStarTransactions`.
 */
export async function POST(request: NextRequest) {
  try {
    requireInternalKey(request);
    const parsed = schema.safeParse(await request.json());
    if (!parsed.success) return jsonError("payment_payload_invalid", 422);
    const input = parsed.data;

    if (input.currency !== "XTR") return jsonError("currency_unsupported", 422);

    // Se verifica la firma pero **no** la antigüedad: una renovación a los seis
    // meses llega con el payload original, y exigir frescura ahí rechazaría
    // precisamente al suscriptor fiel. Lo que sí se exige es que el titular de
    // la factura sea quien paga, o una factura reenviada activaría otra cuenta.
    const payload = verifyBillingPayload(input.invoice_payload, undefined, {
      expectedUserId: input.user_id,
    });
    if (!payload) return jsonError("invoice_payload_rejected", 422);

    const outcome = await applyStarPayment(rawDatabase(), {
      userId: input.user_id,
      chargeId: input.telegram_payment_charge_id,
      starsAmount: input.total_amount,
      invoicePayload: input.invoice_payload,
      isRecurring: input.is_recurring,
      isFirstRecurring: input.is_first_recurring,
      subscriptionExpirationDate: input.subscription_expiration_date,
      raw: input as unknown as Record<string, unknown>,
    }, "bot_forward");

    invalidateEntitlement(input.user_id);
    console.info(JSON.stringify({
      event: "star_payment_applied",
      user_id: input.user_id,
      applied: outcome.applied,
      recurring: input.is_recurring,
    }));
    // 200 también en duplicado: el bot reintenta, y devolverle un error le
    // haría reintentar en bucle algo que ya está aplicado.
    return NextResponse.json({
      applied: outcome.applied,
      status: outcome.applied ? "applied" : "duplicate",
    });
  } catch (error) {
    return authError(error);
  }
}
