import { sql } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { invalidateEntitlement } from "@/lib/auth/entitlements";
import { TelegramApiError, editUserStarSubscription } from "@/lib/billing/telegram";
import { database } from "@/lib/db";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

/**
 * Cancela -o reactiva- la renovación de la suscripción Stars.
 *
 * **No toca `plan` ni `plan_expires_at`**: quien cancela pagó hasta el final
 * del periodo y lo conserva. Lo único que cambia es que el siguiente cobro no
 * llegará, y cuando la fecha pase, `effective_plan` devolverá `free` sola.
 *
 * Telegram exige el cargo **más reciente** para esta operación, no el de la
 * primera compra: por eso `star_subscriptions` persigue `latest_charge_id`.
 */
export async function POST(request: NextRequest) {
  try {
    const session = await authorizeRequest(request, true);
    const body = await request.json().catch(() => ({})) as { resume?: boolean };
    const canceled = body.resume !== true;

    const rows = await database().execute<{ latest_charge_id: string }>(sql`
      SELECT "latest_charge_id" FROM "star_subscriptions"
       WHERE "user_id" = ${session.userId} AND "status" IN ('active', 'canceled')
       LIMIT 1
    `);
    const chargeId = rows[0]?.latest_charge_id;
    if (!chargeId) return jsonError("subscription_not_found", 404);

    await editUserStarSubscription(session.userId, chargeId, canceled);
    await database().execute(sql`
      UPDATE "star_subscriptions"
         SET "status" = ${canceled ? "canceled" : "active"},
             "cancel_requested_at" = ${canceled ? sql`now()` : sql`NULL`},
             "updated_at" = now()
       WHERE "user_id" = ${session.userId}
    `);
    invalidateEntitlement(session.userId);
    return NextResponse.json({ status: canceled ? "canceled" : "active" });
  } catch (error) {
    if (error instanceof TelegramApiError) {
      console.error(JSON.stringify({
        event: "billing_cancel_failed", detail: error.description,
      }));
      return jsonError("billing_cancel_unavailable", 503);
    }
    return authError(error);
  }
}
