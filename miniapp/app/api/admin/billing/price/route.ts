import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { activePlan, setPlanPrice } from "@/lib/billing/plans";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

const schema = z.object({ starsAmount: z.number().int().min(1).max(10000) });

async function requireAdmin(request: NextRequest, mutation: boolean) {
  const session = await authorizeRequest(request, mutation);
  const entitlement = await resolveEntitlement(session.userId);
  if (entitlement.role !== "admin") throw new Error("admin_required");
  return session;
}

export async function GET(request: NextRequest) {
  try {
    await requireAdmin(request, false);
    return NextResponse.json(await activePlan());
  } catch (error) {
    return authError(error);
  }
}

/**
 * Ajusta el precio en caliente.
 *
 * Sin redespliegue a propósito: la economía de Stars puede moverse y el punto
 * de equilibrio del proyecto está en ~15 suscriptores, así que un precio
 * equivocado se paga en meses. Sólo afecta a las facturas emitidas a partir de
 * ahora; las suscripciones ya cobradas conservan su importe hasta que el
 * usuario las cancele y vuelva a suscribirse.
 */
export async function PATCH(request: NextRequest) {
  try {
    await requireAdmin(request, true);
    const parsed = schema.safeParse(await request.json());
    if (!parsed.success) return jsonError("billing_price_invalid", 422);
    const plan = await setPlanPrice(parsed.data.starsAmount);
    console.info(JSON.stringify({
      event: "billing_price_updated", stars_amount: plan.starsAmount,
    }));
    return NextResponse.json(plan);
  } catch (error) {
    return authError(error);
  }
}
