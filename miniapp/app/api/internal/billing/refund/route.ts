import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { invalidateEntitlement } from "@/lib/auth/entitlements";
import { applyStarRefund } from "@/lib/billing/apply";
import { rawDatabase } from "@/lib/db";
import { authError, jsonError, requireInternalKey } from "@/lib/http";

const schema = z.object({
  user_id: z.number().int().positive(),
  telegram_payment_charge_id: z.string().min(1).max(200),
  total_amount: z.number().int().nonnegative().default(0),
});

/**
 * Revoca el acceso tras un reembolso.
 *
 * Entra por aquí tanto lo que inicia el panel de administración como el update
 * `message.refunded_payment` que Telegram entrega cuando el reembolso se hizo
 * desde su lado. Sin este segundo camino, a quien se le devolviera el dinero
 * fuera de la aplicación le quedaría premium hasta fin de periodo.
 *
 * No se exige payload firmado: un reembolso no lo trae. La autoridad aquí es la
 * clave interna más el hecho de que Telegram sólo reembolsa cargos propios.
 */
export async function POST(request: NextRequest) {
  try {
    requireInternalKey(request);
    const parsed = schema.safeParse(await request.json());
    if (!parsed.success) return jsonError("refund_payload_invalid", 422);
    const input = parsed.data;

    const outcome = await applyStarRefund(rawDatabase(), {
      userId: input.user_id,
      chargeId: input.telegram_payment_charge_id,
      starsAmount: input.total_amount,
      raw: input as unknown as Record<string, unknown>,
    }, "bot_forward");

    invalidateEntitlement(input.user_id);
    console.info(JSON.stringify({
      event: "star_refund_applied",
      user_id: input.user_id,
      applied: outcome.applied,
    }));
    return NextResponse.json({
      applied: outcome.applied,
      status: outcome.applied ? "applied" : "duplicate",
    });
  } catch (error) {
    return authError(error);
  }
}
