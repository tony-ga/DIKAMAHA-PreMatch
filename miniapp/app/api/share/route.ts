import { eq } from "drizzle-orm";
import { NextRequest, NextResponse } from "next/server";

import { database } from "@/lib/db";
import { sharedPredictionCards } from "@/lib/db/schema";
import { DikamahaError, dikamahaRequest } from "@/lib/dikamaha";
import { authError, authorizeRequest, jsonError } from "@/lib/http";
import { buildShareCard, shareFixtureKey, shareToken } from "@/lib/share-card";
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
  try {
    const session = await authorizeRequest(request, true);
    const parsed = shareCardSchema.safeParse(await request.json());
    if (!parsed.success) return jsonError("share_request_invalid", 422);
    const input = parsed.data;
    const fixtureKey = shareFixtureKey(input.leagueSlug, input.matchId);

    const existing = await readCard(fixtureKey);
    if (existing) return NextResponse.json({ token: existing, status: "existing" });

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
    const card = buildShareCard(prediction, {
      leagueSlug: input.leagueSlug,
      homeName: input.homeName,
      awayName: input.awayName,
      kickoffTs: kickoff.toISOString(),
    });

    await database().insert(sharedPredictionCards).values({
      fixtureKey,
      token: shareToken(),
      leagueSlug: input.leagueSlug,
      matchId: input.matchId,
      homeTeamName: card.homeName,
      awayTeamName: card.awayName,
      kickoffTs: kickoff,
      payload: card as unknown as Record<string, unknown>,
      createdBy: session.userId,
    }).onConflictDoNothing({ target: sharedPredictionCards.fixtureKey });

    // Se relee en vez de confiar en la inserción: con `onConflictDoNothing`,
    // dos usuarios compartiendo el mismo partido a la vez dejan a uno sin
    // fila insertada, y devolverle su token descartado daría un link muerto.
    const token = await readCard(fixtureKey);
    if (!token) return jsonError("share_card_unavailable", 503);
    return NextResponse.json({ token, status: "created" }, { status: 201 });
  } catch (error) {
    if (error instanceof DikamahaError) {
      console.error("[bff] share prediction unavailable", { status: error.status });
      return jsonError(error.reason ?? "upstream_unavailable",
        error.status >= 500 ? 503 : 422);
    }
    return authError(error);
  }
}

async function readCard(fixtureKey: string): Promise<string | null> {
  const [row] = await database()
    .select({ token: sharedPredictionCards.token })
    .from(sharedPredictionCards)
    .where(eq(sharedPredictionCards.fixtureKey, fixtureKey))
    .limit(1);
  return row?.token ?? null;
}
