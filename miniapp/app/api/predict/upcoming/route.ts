import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { consumePrediction, releasePrediction } from "@/lib/billing/quota";
import { DikamahaError, dikamahaRequest } from "@/lib/dikamaha";
import { authError, authorizeRequest, jsonError } from "@/lib/http";
import { shareFixtureKey } from "@/lib/share-card";
import { predictionRequestSchema } from "@/lib/validation";

/**
 * Predicción pre-match, medida contra el cupo diario del plan gratuito.
 *
 * Dejó de ser un `proxyPost` ciego: para cobrar una unidad hace falta saber de
 * qué partido se trata, así que ahora valida el cuerpo. La clave del cupo es la
 * misma que produce `shareFixtureKey`, de modo que la Mini App, el bot y la
 * tarjeta compartida consumen el mismo presupuesto -un humano, tres superficies,
 * un contador-.
 *
 * El orden es reservar, llamar y liberar si la llamada falla. Cobrar por una
 * predicción que el usuario nunca llegó a ver es el peor fallo posible de esta
 * ruta, y es el único que no se puede reparar solo.
 */
export async function POST(request: NextRequest) {
  let consumedKey: { userId: number; fixtureKey: string } | null = null;
  try {
    const session = await authorizeRequest(request, true);
    const parsed = predictionRequestSchema.safeParse(await request.json());
    if (!parsed.success) return jsonError("prediction_request_invalid", 422);
    const input = parsed.data;

    const entitlement = await resolveEntitlement(session.userId);
    const fixtureKey = shareFixtureKey(input.league_slug, input.match_id);
    const quota = await consumePrediction(
      session.userId, fixtureKey, "miniapp",
      { premium: entitlement.plan === "premium" },
    );
    if (!quota.granted) throw new Error("prediction_quota_exhausted");
    if (quota.reason === "consumed") {
      consumedKey = { userId: session.userId, fixtureKey };
    }

    // Idempotente: calcula una predicción, no muta ningún estado. Ver el
    // comentario de `idempotent` en dikamahaRequest.
    const payload = await dikamahaRequest("/v1/predict/upcoming", {
      method: "POST",
      body: JSON.stringify(input),
    }, true);
    return NextResponse.json({
      ...(payload as Record<string, unknown>),
      quota: { remaining: quota.remaining, reason: quota.reason },
    });
  } catch (error) {
    // Sólo se devuelve la unidad si esta petición llegó a gastarla. Un
    // `replay` no consumió nada y devolverlo restaría de más.
    if (consumedKey) {
      await releasePrediction(consumedKey.userId, consumedKey.fixtureKey)
        .catch(() => undefined);
    }
    if (error instanceof DikamahaError) {
      console.error("[bff] upstream POST unavailable", {
        path: "/v1/predict/upcoming", status: error.status,
      });
      return jsonError(
        error.reason ?? "upstream_unavailable",
        error.status === 429 ? 429 : error.status >= 500 ? 503 : 422);
    }
    return authError(error);
  }
}
