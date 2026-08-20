import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { activePlan } from "@/lib/billing/plans";
import { quotaSnapshot } from "@/lib/billing/quota";
import { stripeEnabled } from "@/lib/env";
import { authError, authorizeRequest } from "@/lib/http";

/**
 * Estado de plan y cupo del usuario actual.
 *
 * Existe porque el cliente no puede fiarse del `plan` que viaja en la cookie:
 * dura 30 días y no se relee. Esta ruta es la única fuente que la interfaz
 * consulta para decidir si pinta un muro, un contador o nada.
 */
export async function GET(request: NextRequest) {
  try {
    const session = await authorizeRequest(request);
    const entitlement = await resolveEntitlement(session.userId);
    const premium = entitlement.plan === "premium";
    const [plan, quota] = await Promise.all([
      activePlan(),
      // Un premium no tiene contador, y consultarlo sólo para descartarlo
      // sería un viaje a la base por cada pintado.
      premium ? Promise.resolve(null) : quotaSnapshot(session.userId),
    ]);
    return NextResponse.json({
      plan: entitlement.plan,
      planSource: entitlement.planSource,
      role: entitlement.role,
      expiresAt: entitlement.expiresAt?.toISOString() ?? null,
      enforced: entitlement.enforced,
      starsAmount: plan.starsAmount,
      // Si el cobro web está disponible. El cliente no puede deducirlo: sabe
      // que corre en un navegador, pero no si este servicio tiene Stripe
      // configurado. Sin esto, el botón de compra en la web sería un enlace a
      // ninguna parte cada vez que el interruptor esté apagado.
      webCheckout: stripeEnabled(),
      quota,
    });
  } catch (error) {
    return authError(error);
  }
}
