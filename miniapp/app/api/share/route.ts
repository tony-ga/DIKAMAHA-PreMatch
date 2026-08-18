import { eq } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { resolveEntitlement } from "@/lib/auth/entitlements";
import { consumePrediction, releasePrediction } from "@/lib/billing/quota";
import { database } from "@/lib/db";
import { sharedPredictionCards } from "@/lib/db/schema";
import { DikamahaError, dikamahaRequest } from "@/lib/dikamaha";
import { authError, authorizeRequest, jsonError } from "@/lib/http";
import { record } from "@/lib/client-api";
import {
  SHARE_CARD_VERSION, buildShareCard, shareFixtureKey, shareToken,
} from "@/lib/share-card";
import { shareLogoDataUri } from "@/lib/share-logo";
import { shareCardSchema } from "@/lib/validation";

/**
 * Congela una tarjeta pre-match y devuelve su link público.
 *
 * Crear la tarjeta exige sesión y CSRF -sólo quien tiene acceso puede publicar
 * un partido-, pero leerla no: ese es justamente el punto del link. Ver
 * `app/s/[token]`.
 *
 * Idempotente por partido. Si el partido ya tiene tarjeta se devuelve la
 * existente sin volver a predecir: la primera congelación es la que circula, y
 * dos imágenes del mismo encuentro con cifras distintas -congeladas con horas
 * de diferencia- contradirían la premisa de la predicción sellada.
 */
export async function POST(request: NextRequest) {
  let consumedKey: { userId: number; fixtureKey: string } | null = null;
  try {
    const session = await authorizeRequest(request, true);
    const parsed = shareCardSchema.safeParse(await request.json());
    if (!parsed.success) return jsonError("share_request_invalid", 422);
    const input = parsed.data;
    const fixtureKey = shareFixtureKey(input.leagueSlug, input.matchId);

    const existing = await readCard(fixtureKey);
    if (existing && existing.version === SHARE_CARD_VERSION) {
      return NextResponse.json({ token: existing.token, status: "existing" });
    }

    // Sólo se cobra cuota cuando hay que predecir de verdad. Servir una tarjeta
    // ya congelada no cuesta nada, y por eso la comprobación va después de
    // `readCard`. Sin esta puerta, "compartir" sería una vía para pedir
    // predicciones ilimitadas sin pasar por el contador: es la misma llamada a
    // `/v1/predict/upcoming` que la ruta medida, sólo que por otra puerta.
    const entitlement = await resolveEntitlement(session.userId);
    const quota = await consumePrediction(
      session.userId, fixtureKey, "share",
      { premium: entitlement.plan === "premium" },
    );
    if (!quota.granted) throw new Error("prediction_quota_exhausted");
    if (quota.reason === "consumed") {
      consumedKey = { userId: session.userId, fixtureKey };
    }

    const prediction = await dikamahaRequest("/v1/predict/upcoming", {
      method: "POST",
      body: JSON.stringify({
        match_id: input.matchId,
        league_slug: input.leagueSlug,
        home_team_id: input.homeTeamId,
        away_team_id: input.awayTeamId,
        kickoff_ts: input.kickoffTs,
      }),
    }, true);

    const kickoff = new Date(input.kickoffTs);
    if (!Number.isFinite(kickoff.getTime())) return jsonError("share_kickoff_invalid", 422);
    // En paralelo: los dos escudos son independientes entre sí y de todo lo
    // demás, y `shareLogoDataUri` ya degrada a cadena vacía por su cuenta.
    const [homeLogo, awayLogo] = await Promise.all([
      shareLogoDataUri(input.homeLogo),
      shareLogoDataUri(input.awayLogo),
    ]);
    const card = buildShareCard(prediction, {
      leagueSlug: input.leagueSlug,
      homeName: input.homeName,
      awayName: input.awayName,
      kickoffTs: kickoff.toISOString(),
      homeLogo,
      awayLogo,
    });
    const payload = card as unknown as Record<string, unknown>;

    // Una tarjeta congelada con un formato que la aplicación ya no sabe pintar
    // se reconstruye en su sitio, conservando su token. No contradice que "la
    // primera congelación es la que circula" (DEC-195): no gana una predicción
    // más fresca, se repara un link que de otro modo quedaría muerto -
    // `shareCardByToken` no sirve una versión que no es la vigente-.
    if (existing) {
      await database().update(sharedPredictionCards).set({
        homeTeamName: card.home.name,
        awayTeamName: card.away.name,
        payload,
      }).where(eq(sharedPredictionCards.fixtureKey, fixtureKey));
      return NextResponse.json({ token: existing.token, status: "rebuilt" });
    }

    await database().insert(sharedPredictionCards).values({
      fixtureKey,
      token: shareToken(),
      leagueSlug: input.leagueSlug,
      matchId: input.matchId,
      homeTeamName: card.home.name,
      awayTeamName: card.away.name,
      kickoffTs: kickoff,
      payload,
      createdBy: session.userId,
    }).onConflictDoNothing({ target: sharedPredictionCards.fixtureKey });

    // Se relee en vez de confiar en la inserción: con `onConflictDoNothing`,
    // dos usuarios compartiendo el mismo partido a la vez dejan a uno sin
    // fila insertada, y devolverle su token descartado daría un link muerto.
    const stored = await readCard(fixtureKey);
    if (!stored) return jsonError("share_card_unavailable", 503);
    return NextResponse.json({ token: stored.token, status: "created" }, { status: 201 });
  } catch (error) {
    if (consumedKey) {
      await releasePrediction(consumedKey.userId, consumedKey.fixtureKey)
        .catch(() => undefined);
    }
    if (error instanceof DikamahaError) {
      console.error("[bff] share prediction unavailable", { status: error.status });
      return jsonError(error.reason ?? "upstream_unavailable",
        error.status >= 500 ? 503 : 422);
    }
    return authError(error);
  }
}

/** Token y versión de formato de la tarjeta ya congelada, si existe. */
async function readCard(
  fixtureKey: string,
): Promise<{ token: string; version: number } | null> {
  const [row] = await database()
    .select({
      token: sharedPredictionCards.token,
      payload: sharedPredictionCards.payload,
    })
    .from(sharedPredictionCards)
    .where(eq(sharedPredictionCards.fixtureKey, fixtureKey))
    .limit(1);
  if (!row) return null;
  return { token: row.token, version: Number(record(row.payload).version) };
}
