import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { consumePrediction, releasePrediction } from "@/lib/billing/quota";
import { authError, jsonError, requireInternalKey } from "@/lib/http";

const schema = z.object({
  user_id: z.number().int().positive(),
  fixture_key: z.string().trim().min(3).max(120),
  release: z.boolean().default(false),
});

/**
 * Consume -o devuelve- una unidad del cupo diario, a petición del bot.
 *
 * Es lo que hace que "3 predicciones al día" sea por persona y no por
 * superficie: el bot y la Mini App incrementan el mismo contador en PostgreSQL,
 * usando la misma clave `league_slug:match_id`. Sin esta ruta, alguien tendría
 * tres en cada sitio.
 */
export async function POST(request: NextRequest) {
  try {
    requireInternalKey(request);
    const parsed = schema.safeParse(await request.json());
    if (!parsed.success) return jsonError("quota_request_invalid", 422);
    const input = parsed.data;

    if (input.release) {
      await releasePrediction(input.user_id, input.fixture_key);
      return NextResponse.json({ released: true });
    }

    const entitlement = await resolveEntitlement(input.user_id);
    const outcome = await consumePrediction(
      input.user_id, input.fixture_key, "bot",
      { premium: entitlement.plan === "premium" },
    );
    return NextResponse.json({
      granted: outcome.granted,
      reason: outcome.reason,
      remaining: outcome.remaining,
    });
  } catch (error) {
    return authError(error);
  }
}
