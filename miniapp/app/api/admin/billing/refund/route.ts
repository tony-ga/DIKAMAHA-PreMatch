import { sql } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { invalidateEntitlement, resolveEntitlement } from "@/lib/auth/entitlements";
import { applyStarRefund } from "@/lib/billing/apply";
import { TelegramApiError, refundStarPayment } from "@/lib/billing/telegram";
import { database, rawDatabase } from "@/lib/db";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

const schema = z.object({ userId: z.number().int().positive() });

/**
 * Reembolsa la última suscripción de un usuario y le retira el acceso.
 *
 * Telegram entregará además un `refunded_payment` al bot, que lo reenviará al
 * endpoint interno; la puerta del libro mayor hace que ese segundo camino sea
 * inofensivo en lugar de un doble reembolso contable.
 */
export async function POST(request: NextRequest) {
  try {
    const session = await authorizeRequest(request, true);
    const admin = await resolveEntitlement(session.userId);
    if (admin.role !== "admin") throw new Error("admin_required");

    const parsed = schema.safeParse(await request.json());
    if (!parsed.success) return jsonError("refund_request_invalid", 422);
    const { userId } = parsed.data;

    const rows = await database().execute<{
      latest_charge_id: string; stars_amount: number;
    }>(sql`
      SELECT "latest_charge_id", "stars_amount" FROM "star_subscriptions"
       WHERE "user_id" = ${userId} LIMIT 1
    `);
    const subscription = rows[0];
    if (!subscription) return jsonError("subscription_not_found", 404);

    await refundStarPayment(userId, subscription.latest_charge_id);
    await applyStarRefund(rawDatabase(), {
      userId,
      chargeId: subscription.latest_charge_id,
      starsAmount: Number(subscription.stars_amount),
      raw: { initiated_by: session.userId },
    }, "manual");
    invalidateEntitlement(userId);
    return NextResponse.json({ status: "refunded" });
  } catch (error) {
    if (error instanceof TelegramApiError) {
      console.error(JSON.stringify({
        event: "billing_refund_failed", detail: error.description,
      }));
      return jsonError("billing_refund_unavailable", 503);
    }
    return authError(error);
  }
}
