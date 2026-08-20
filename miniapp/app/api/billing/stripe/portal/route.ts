import { NextRequest, NextResponse } from "next/server";

import { createPortalSession } from "@/lib/billing/stripe";
import { gatewayStatus } from "@/lib/billing/stripe-apply";
import { rawDatabase } from "@/lib/db";
import { publicWebUrl, stripeEnabled } from "@/lib/env";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

/**
 * Portal de facturación de Stripe: cancelar, cambiar tarjeta, ver recibos.
 *
 * Se delega en Stripe en vez de construir esas pantallas aquí porque la
 * cancelación tiene que ser tan fácil como la compra, y el portal alojado ya lo
 * es -además de ser el único sitio donde los datos de pago están de verdad-.
 */
export async function POST(request: NextRequest) {
  if (!stripeEnabled()) return jsonError("stripe_disabled", 503);
  try {
    const session = await authorizeRequest(request, true);
    const gateways = await gatewayStatus(rawDatabase(), session.userId);
    if (!gateways.stripeCustomerId) return jsonError("stripe_customer_missing", 404);
    const portal = await createPortalSession(
      gateways.stripeCustomerId, `${publicWebUrl()}/subscriptions`);
    return NextResponse.json({ url: portal.url });
  } catch (error) {
    if (error instanceof Error && error.message === "stripe_request_failed") {
      return jsonError("billing_portal_unavailable", 503);
    }
    return authError(error);
  }
}
