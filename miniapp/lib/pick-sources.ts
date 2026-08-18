/**
 * Traduce lo que las pantallas de prediccion ya pintan a selecciones del
 * Constructor de Picks (DEC-208).
 *
 * El constructor no consulta ningun endpoint propio: toma exactamente los
 * campos del payload de `/v1/predict/upcoming` que el usuario esta viendo, de
 * modo que la probabilidad que suma es la misma que leyo en pantalla.
 */

import { metricLabel, teamLabel } from "@/lib/audited-ladder";
import { record } from "@/lib/client-api";
import { periodLabel } from "@/lib/market-grid";
import type {
  CountPick, GoalContext, GoalMarketKey, GoalPick, MatchRef,
} from "@/lib/pick-builder";

const GOAL_LABELS: Record<GoalMarketKey, (match: MatchRef) => string> = {
  home: (match) => `Gana ${match.homeName}`,
  draw: () => "Empate",
  away: (match) => `Gana ${match.awayName}`,
  over_2_5: () => "Más de 2.5 goles",
  under_2_5: () => "Menos de 2.5 goles",
  btts_yes: () => "Ambos marcan",
  btts_no: () => "No marcan ambos",
};

function finite(value: unknown): number {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : Number.NaN;
}

/**
 * Extrae el contexto de goles del payload.
 *
 * Devuelve `null` cuando faltan las lambdas: sin ellas no hay matriz de
 * marcadores que sumar, y es preferible no ofrecer el boton "+" en los
 * mercados de gol antes que ofrecerlo y devolver despues un producto
 * incorrecto. `tau` puede faltar legitimamente -la via de respaldo del motor
 * usa dos Poisson independientes-, y entonces vale cero.
 */
export function goalContextFrom(payload: Record<string, unknown>): GoalContext | null {
  const audit = record(payload.audit);
  const context: GoalContext = {
    lambdaHome: finite(payload.lambda_home),
    lambdaAway: finite(payload.lambda_away),
    tau: Number.isFinite(Number(audit.tau_dc)) ? Number(audit.tau_dc) : 0,
    probabilityHome: finite(payload.probability_home),
    probabilityDraw: finite(payload.probability_draw),
    probabilityAway: finite(payload.probability_away),
    probabilityOver25: finite(payload.probability_over_2_5),
    probabilityBtts: finite(payload.probability_btts),
  };
  const values = Object.values(context);
  if (values.some((value) => !Number.isFinite(value))) return null;
  if (context.lambdaHome <= 0 || context.lambdaAway <= 0) return null;
  return context;
}

/** Probabilidad publicada de cada mercado de gol, dentro del propio contexto. */
export function goalProbability(context: GoalContext, market: GoalMarketKey): number {
  if (market === "home") return context.probabilityHome;
  if (market === "draw") return context.probabilityDraw;
  if (market === "away") return context.probabilityAway;
  if (market === "over_2_5") return context.probabilityOver25;
  if (market === "under_2_5") return 1 - context.probabilityOver25;
  if (market === "btts_yes") return context.probabilityBtts;
  return 1 - context.probabilityBtts;
}

export function goalPick(
  match: MatchRef, context: GoalContext, market: GoalMarketKey,
): GoalPick {
  return {
    id: `${match.matchId}|goal|${market}`,
    kind: "goal",
    match,
    label: GOAL_LABELS[market](match),
    probability: goalProbability(context, market),
    market,
    goals: context,
    addedAt: Date.now(),
  };
}

/**
 * Cabecera de un grupo de escalera, tal como llega del payload.
 *
 * Los campos son `unknown` porque las dos escaleras -auditada y rejilla- se
 * normalizan de formas distintas en sus componentes, y lo unico que el
 * constructor necesita de ellas es la identidad de la variable.
 */
type LadderGroup = {
  key?: unknown;
  metric?: unknown;
  team_side?: unknown;
  period?: unknown;
};

/** Nombre legible del grupo: es la identidad de la variable aleatoria. */
export function groupLabel(
  group: LadderGroup, homeName: string, awayName: string,
): string {
  return [
    periodLabel(String(group.period)),
    teamLabel(String(group.team_side), homeName, awayName),
    metricLabel(String(group.metric)),
  ].join(" · ");
}

/**
 * Construye una seleccion de conteo.
 *
 * `overProbability` viaja siempre, tambien en las selecciones "menos de":
 * combinar dos lineas de la misma variable exige la escalera en una sola
 * direccion, no la probabilidad que se mostro.
 */
export function countPick(
  match: MatchRef, group: LadderGroup, line: number, overProbability: number,
  direction: "over" | "under", source: string,
): CountPick | null {
  if (!Number.isFinite(line) || !Number.isFinite(overProbability)) return null;
  const groupKey = String(group.key || `${group.team_side}_${group.metric}_${group.period}`);
  const label = [
    groupLabel(group, match.homeName, match.awayName),
    direction === "over" ? `Más de ${line}` : `Menos de ${line}`,
  ].join(" · ");
  return {
    id: `${match.matchId}|count|${groupKey}|${line}|${direction}`,
    kind: "count",
    match,
    label,
    probability: direction === "over" ? overProbability : 1 - overProbability,
    groupKey,
    groupLabel: groupLabel(group, match.homeName, match.awayName),
    line,
    direction,
    overProbability,
    source,
    addedAt: Date.now(),
  };
}
