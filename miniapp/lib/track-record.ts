import { record } from "@/lib/client-api";
import { metricLabel, teamLabel } from "@/lib/audited-ladder";

const SHADOW_METRIC_LABELS: Record<string, string> = {
  shots: "Tiros", shots_on_target: "Tiros a puerta", corners: "Córners",
  yellow_cards: "Tarjetas amarillas", red_cards: "Tarjetas rojas",
};
const SHADOW_PERIOD_LABELS: Record<string, string> = {
  first_half: "1ª mitad", second_half: "2ª mitad", full_match: "partido completo",
};

const HIGH_PROBABILITY_GOAL_MARKET_LABELS: Record<string, string> = {
  "1x2": "Resultado (1X2)", over_2_5: "Más de 2.5 goles", btts: "Ambos marcan",
};

export const SHADOW_PREVIEW_SIZE = 4;

/** Zona con la que el backend define "el día", igual que `settlement_store.on_date`. */
const CHANNEL_TIMEZONE = "America/Mexico_City";

/**
 * Fecha `YYYYMMDD` del día en curso en Ciudad de México.
 *
 * `/v1/track-record/daily` agrupa por la fecha **local del kickoff**
 * (`store.on_date(target, MEXICO_TZ)`), así que pedir la fecha UTC del
 * navegador desalineaba la ventana con el servidor durante las seis horas de
 * offset: a partir de las 18:00 de México el día ya había avanzado en UTC y
 * "Resultados de hoy" consultaba mañana -justo la franja en que se liquidan
 * los partidos de la tarde-noche, de modo que los aciertos del día
 * desaparecían de la ventana en el momento en que empezaban a existir.
 * `en-CA` se elige por su formato ISO (`YYYY-MM-DD`), no por idioma.
 */
export function channelDateParam(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: CHANNEL_TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(now).replaceAll("-", "");
}

/**
 * Traduce una clave de mercado shadow liquidado a una etiqueta legible.
 *
 * La clave se construye en `telegram_channel_publisher.py::_snapshot_lines`
 * como `{home|away|total}_{metrica}_{periodo}_over_{linea_con_guion_bajo}`
 * (ej. `home_corners_first_half_over_4_5`); aquí se deshace ese formato para
 * mostrar "Puebla · Córners · 1ª mitad" en vez de la clave cruda. Una clave
 * que no calce el patrón se degrada a guiones bajos por espacios en vez de
 * fallar, igual que `metricLabel` en `audited-ladder.ts`.
 */
export function shadowMarketLabel(key: string, homeName: string, awayName: string): string {
  const match = key.match(/^(home|away|total)_(.+)_over_[\d_]+$/);
  if (!match) return key.replaceAll("_", " ");
  const [, side, rest] = match;
  const period = Object.keys(SHADOW_PERIOD_LABELS).find(
    (candidate) => rest === candidate || rest.endsWith(`_${candidate}`));
  const metric = period && rest !== period ? rest.slice(0, -(period.length + 1)) : rest;
  const metricName = SHADOW_METRIC_LABELS[metric] ?? metric.replaceAll("_", " ");
  const teamName = side === "home" ? homeName : side === "away" ? awayName : `${homeName} + ${awayName}`;
  const periodName = period ? SHADOW_PERIOD_LABELS[period] : "";
  return [teamName, metricName, periodName].filter(Boolean).join(" · ");
}

/** Normaliza los veredictos shadow de un partido a filas con su clave. */
export function shadowMatchEntries(value: unknown): Array<Record<string, unknown> & { key: string }> {
  return Object.entries(record(value)).map(([key, entry]) => ({ key, ...record(entry) }));
}

/** Suma aciertos y total del bloque agregado `shadow.markets` de un periodo. */
export function shadowSummary(shadow: unknown): { hits: number; total: number } {
  const markets = record(record(shadow).markets);
  return Object.values(markets).map(record).reduce<{ hits: number; total: number }>(
    (acc, entry) => ({
      hits: acc.hits + Number(entry.hits ?? 0),
      total: acc.total + Number(entry.total ?? 0),
    }),
    { hits: 0, total: 0 },
  );
}

/** Extrae la lista de picks de Fase 123 (`GET /v1/track-record[...]`). */
export function highProbabilityPicks(value: unknown): Array<Record<string, unknown>> {
  const picks = record(value).picks;
  return Array.isArray(picks) ? picks.map(record) : [];
}

/**
 * Traduce un pick de "Mayor probabilidad" ya congelado a una etiqueta legible.
 *
 * Reutiliza `metricLabel`/`teamLabel` de la escalera auditada -mismo
 * vocabulario, sin duplicar el diccionario- porque `market`, `direction`,
 * `metric`, `team_side`, `period` y `line` de un pick son exactamente los
 * que `freeze_from_pick` congeló del menú, sin recalcular nada.
 */
export function highProbabilityPickLabel(
  entry: Record<string, unknown>, homeName: string, awayName: string,
): string {
  const market = String(entry.market ?? "");
  const goalLabel = HIGH_PROBABILITY_GOAL_MARKET_LABELS[market];
  if (goalLabel) return goalLabel;
  const side = teamLabel(String(entry.team_side ?? ""), homeName, awayName);
  const metric = metricLabel(String(entry.metric ?? ""));
  const period = SHADOW_PERIOD_LABELS[String(entry.period ?? "")] ?? "";
  const base = [side, metric, period].filter(Boolean).join(" · ");
  const line = entry.line;
  if (line === null || line === undefined) return base;
  const direction = entry.direction === "under" ? "Menos de" : "Más de";
  return `${base} · ${direction} ${Number(line)}`;
}
