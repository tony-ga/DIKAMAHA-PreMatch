import { z } from "zod";

export const favoriteSchema = z.object({
  entityType: z.enum(["fixture", "team", "league"]),
  entityId: z.string().trim().min(1).max(100),
  label: z.string().trim().min(1).max(100),
  metadata: z.record(z.string(), z.unknown()).default({}),
});

/**
 * Identidad del partido que se quiere compartir.
 *
 * Son los mismos campos que `/v1/predict/upcoming` exige, porque la tarjeta se
 * congela desde esa respuesta y no desde nada que mande el cliente: el cuerpo
 * sólo dice *qué* partido, nunca *qué* probabilidades.
 */
export const shareCardSchema = z.object({
  matchId: z.number().int().positive(),
  leagueSlug: z.string().trim().regex(/^[A-Za-z0-9._]+$/),
  homeTeamId: z.number().int().positive(),
  awayTeamId: z.number().int().positive(),
  kickoffTs: z.string().trim().min(10).max(40),
  homeName: z.string().trim().max(80).default(""),
  awayName: z.string().trim().max(80).default(""),
  // URLs de escudo. No se visitan directamente: viajan al proxy
  // `/v1/media/image`, que valida host, tamaño y firma PNG antes de devolver
  // nada (ver `lib/share-logo.ts`).
  homeLogo: z.string().trim().max(500).default(""),
  awayLogo: z.string().trim().max(500).default(""),
});

/**
 * Cuerpo de una petición de predicción pre-match.
 *
 * La ruta era un proxy ciego hasta la Fase 125. Ahora tiene que validar,
 * porque el plan gratuito concede 3 predicciones al día **por partido** y sin
 * leer el cuerpo no hay clave contra la que cobrar. Llega en `snake_case`
 * porque así lo manda el cliente y así lo espera `/v1/predict/upcoming`.
 */
export const predictionRequestSchema = z.object({
  match_id: z.number().int().positive(),
  league_slug: z.string().trim().regex(/^[A-Za-z0-9._]+$/),
  home_team_id: z.number().int().positive(),
  away_team_id: z.number().int().positive(),
  kickoff_ts: z.string().trim().min(10).max(40),
});

export const alertRuleTypes = [
  "kickoff",
  "score_change",
  "status_change",
  "probability_delta",
  "model_status",
  "market_threshold",
  "fixture_presence",
] as const;

export const marketKeys = [
  "probability_home",
  "probability_draw",
  "probability_away",
  "probability_over_2_5",
  "probability_btts",
  "home_corners_over_4_5",
  "away_corners_over_4_5",
  "away_shots_over_10_5",
  "shots_on_target_total_over_7_5",
  "away_shots_second_half_over_5_5",
  "home_corners_second_half_over_2_5",
  "home_shots_first_half_over_5_5",
  "home_shots_second_half_over_5_5",
] as const;

export const subscriptionSchema = z.object({
  ruleType: z.enum(alertRuleTypes),
  fixtureId: z.string().trim().min(1).max(100).optional(),
  leagueSlug: z.string().trim().regex(/^[A-Za-z0-9._]+$/),
  marketKey: z.enum(marketKeys).optional(),
  period: z.enum(["first_half", "second_half", "full_match", "live"]).optional(),
  selection: z.string().trim().max(64).optional(),
  comparator: z.enum(["gte", "lte", "delta"]).optional(),
  threshold: z.number().min(0).max(1).optional(),
  cooldownSeconds: z.number().int().min(300).max(86_400).default(300),
  enabled: z.boolean().default(true),
}).superRefine((value, context) => {
  if (["market_threshold", "probability_delta"].includes(value.ruleType)) {
    if (!value.marketKey || value.threshold === undefined || !value.comparator) {
      context.addIssue({ code: "custom", message: "market_rule_incomplete" });
    }
  }
  if (value.ruleType === "probability_delta" && value.comparator !== "delta") {
    context.addIssue({ code: "custom", message: "probability_delta_requires_delta" });
  }
});

export const subscriptionPatchSchema = z.object({
  ruleType: z.enum(alertRuleTypes).optional(),
  fixtureId: z.string().trim().min(1).max(100).optional(),
  leagueSlug: z.string().trim().regex(/^[A-Za-z0-9._]+$/).optional(),
  marketKey: z.enum(marketKeys).optional(),
  period: z.enum(["first_half", "second_half", "full_match", "live"]).optional(),
  selection: z.string().trim().max(64).optional(),
  comparator: z.enum(["gte", "lte", "delta"]).optional(),
  threshold: z.number().min(0).max(1).optional(),
  cooldownSeconds: z.number().int().min(300).max(86_400).optional(),
  enabled: z.boolean().optional(),
}).strict().superRefine((value, context) => {
  if (Object.keys(value).length === 0) {
    context.addIssue({ code: "custom", message: "empty_patch" });
  }
});
