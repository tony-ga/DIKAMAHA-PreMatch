import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { quotaSnapshot } from "@/lib/billing/quota";
import { authError, jsonError, requireInternalKey } from "@/lib/http";

/**
 * Plan y cupo de un usuario, para el bot.
 *
 * El bot no tiene base de datos por diseño (Fase 109), así que pregunta. Cachea
 * la respuesta 60 s y degrada a `free` si esta ruta no contesta: nunca cierra
 * el acceso por un fallo nuestro.
 */
export async function GET(request: NextRequest) {
  try {
    requireInternalKey(request);
    const raw = request.nextUrl.searchParams.get("user_id");
    const userId = Number(raw);
    if (!Number.isSafeInteger(userId) || userId <= 0) {
      return jsonError("user_id_invalid", 422);
    }
    const entitlement = await resolveEntitlement(userId);
    const premium = entitlement.plan === "premium";
    return NextResponse.json({
      plan: entitlement.plan,
      enforced: entitlement.enforced,
      expires_at: entitlement.expiresAt?.toISOString() ?? null,
      plan_source: entitlement.planSource,
      // `null` significa ilimitado, no cero. El bot lo interpreta así.
      remaining_predictions: premium
        ? null
        : (await quotaSnapshot(userId)).remaining,
    });
  } catch (error) {
    return authError(error);
  }
}
