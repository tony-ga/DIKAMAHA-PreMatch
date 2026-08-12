import { record } from "@/lib/client-api";

const SHADOW_METRIC_LABELS: Record<string, string> = {
  shots: "Tiros", shots_on_target: "Tiros a puerta", corners: "Córners",
  yellow_cards: "Tarjetas amarillas", red_cards: "Tarjetas rojas",
};
const SHADOW_PERIOD_LABELS: Record<string, string> = {
  first_half: "1ª mitad", second_half: "2ª mitad", full_match: "partido completo",
};

export const SHADOW_PREVIEW_SIZE = 4;

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
