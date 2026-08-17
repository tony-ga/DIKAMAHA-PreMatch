import { NextRequest, NextResponse } from "next/server";

import type { Feature } from "@/lib/auth/entitlements";
import { DikamahaError, dikamahaRequest } from "@/lib/dikamaha";
import { authError, authorizeFeature, authorizeRequest, jsonError } from "@/lib/http";

/**
 * `feature` es opcional: sin él la ruta queda gratuita, que es lo que sigue
 * siendo la mayoría -catálogo, historial, explorador-. Marcar una ruta como de
 * pago es añadir un argumento, y quitarle el muro es borrarlo.
 */
export async function proxyGet(request: NextRequest, path: string, feature?: Feature) {
  try {
    if (feature) await authorizeFeature(request, feature);
    else await authorizeRequest(request);
    const payload = await dikamahaRequest(`${path}${request.nextUrl.search}`);
    return NextResponse.json(payload);
  } catch (error) {
    if (error instanceof DikamahaError) {
      console.error("[bff] upstream GET unavailable", { path, status: error.status });
      return jsonError(
        error.reason ?? "upstream_unavailable",
        error.status === 429 ? 429 : error.status >= 500 ? 503 : 422);
    }
    return authError(error);
  }
}

export async function proxyPost(
  request: NextRequest, path: string, idempotent = false, feature?: Feature,
) {
  try {
    if (feature) await authorizeFeature(request, feature, true);
    else await authorizeRequest(request, true);
    const body = await request.json();
    const payload = await dikamahaRequest(path, {
      method: "POST",
      body: JSON.stringify(body),
    }, idempotent);
    return NextResponse.json(payload);
  } catch (error) {
    if (error instanceof DikamahaError) {
      console.error("[bff] upstream POST unavailable", { path, status: error.status });
      return jsonError(
        error.reason ?? "upstream_unavailable",
        error.status === 429 ? 429 : error.status >= 500 ? 503 : 422);
    }
    return authError(error);
  }
}
