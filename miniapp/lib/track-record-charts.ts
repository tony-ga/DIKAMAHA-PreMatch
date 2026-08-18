import { record } from "@/lib/client-api";

/** Mismo huso que agrupa "el día" en el backend (`settlement_store.on_date`). */
const CHANNEL_TIMEZONE = "America/Mexico_City";

const OFFICIAL_MARKET_LABELS: Record<string, string> = {
  one_x_two: "Resultado (1X2)",
  over_2_5: "Más de 2.5 goles",
  btts: "Ambos marcan",
};

export type MarketRatePoint = {
  key: string;
  label: string;
  rate: number;
  baseline: number;
  hits: number;
  total: number;
  sufficientSample: boolean;
};

/**
 * Tasa de acierto por mercado oficial, con su referencia.
 *
 * Un mercado sin muestra suficiente (`sufficient_sample = false`, umbral
 * `MINIMUM_SAMPLE = 20` en `settlement_store.py`) queda fuera: un porcentaje
 * sobre 6 partidos no es un porcentaje, es ruido con forma de porcentaje, y
 * graficarlo igual que uno con 40 lo haría parecer igual de fiable.
 */
export function officialMarketRateSeries(official: unknown): MarketRatePoint[] {
  const block = record(official);
  return Object.entries(OFFICIAL_MARKET_LABELS).flatMap(([key, label]) => {
    const market = record(block[key]);
    if (!market.sufficient_sample) return [];
    const rate = Number(market.rate);
    const baseline = Number(market.baseline_rate);
    if (!Number.isFinite(rate)) return [];
    return [{
      key, label, rate,
      baseline: Number.isFinite(baseline) ? baseline : 0,
      hits: Number(market.hits ?? 0),
      total: Number(market.total ?? 0),
      sufficientSample: true,
    }];
  });
}

export type DailyRatePoint = {
  date: string;
  label: string;
  hits: number;
  total: number;
  rate: number;
};

function localDay(kickoffTs: unknown): string | null {
  if (typeof kickoffTs !== "string") return null;
  const date = new Date(kickoffTs);
  if (!Number.isFinite(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: CHANNEL_TIMEZONE, year: "numeric", month: "2-digit", day: "2-digit",
  }).format(date);
}

/**
 * Tasa de acierto agregada por día, sobre los tres mercados oficiales juntos.
 *
 * Agrega los tres mercados en una sola tasa diaria en vez de trazar tres
 * líneas: con una ventana de 200 partidos, tres series superpuestas se leen
 * peor que una sola con la lectura correcta -"¿el conjunto acierta más unos
 * días que otros?"-, y el desglose por mercado ya está en la gráfica de
 * barras de arriba.
 */
export function dailyHitRateSeries(matches: unknown): DailyRatePoint[] {
  const rows = Array.isArray(matches) ? matches.map(record) : [];
  const byDay = new Map<string, { hits: number; total: number }>();
  for (const row of rows) {
    const day = localDay(row.kickoff_ts);
    if (!day) continue;
    const verdicts = record(row.official_verdicts);
    const bucket = byDay.get(day) ?? { hits: 0, total: 0 };
    for (const key of Object.keys(OFFICIAL_MARKET_LABELS)) {
      const verdict = record(verdicts[key]);
      if (verdict.hit === undefined) continue;
      bucket.total += 1;
      if (verdict.hit) bucket.hits += 1;
    }
    byDay.set(day, bucket);
  }
  return Array.from(byDay.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, bucket]) => ({
      date,
      label: new Date(`${date}T12:00:00`).toLocaleDateString("es-MX", { day: "2-digit", month: "short" }),
      hits: bucket.hits,
      total: bucket.total,
      rate: bucket.total > 0 ? bucket.hits / bucket.total : 0,
    }))
    .filter((point) => point.total > 0);
}

export type LeagueRatePoint = {
  league: string;
  hits: number;
  total: number;
  rate: number;
};

/**
 * Tasa de acierto por liga, sobre los tres mercados oficiales juntos.
 *
 * Ordena por volumen de partidos verificados, no por tasa: una liga con dos
 * partidos y un acierto mostraría 100% arriba de todas, que es exactamente el
 * tipo de lectura engañosa que las barras de "muestra insuficiente" ya evitan
 * en el resumen por mercado. Se limita a las ligas con más historial para que
 * la gráfica quepa sin desplazamiento horizontal.
 */
export function leagueHitRateSeries(matches: unknown, limit = 8): LeagueRatePoint[] {
  const rows = Array.isArray(matches) ? matches.map(record) : [];
  const byLeague = new Map<string, { hits: number; total: number }>();
  for (const row of rows) {
    const league = typeof row.league_slug === "string" ? row.league_slug : null;
    if (!league) continue;
    const verdicts = record(row.official_verdicts);
    const bucket = byLeague.get(league) ?? { hits: 0, total: 0 };
    for (const key of Object.keys(OFFICIAL_MARKET_LABELS)) {
      const verdict = record(verdicts[key]);
      if (verdict.hit === undefined) continue;
      bucket.total += 1;
      if (verdict.hit) bucket.hits += 1;
    }
    byLeague.set(league, bucket);
  }
  return Array.from(byLeague.entries())
    .map(([league, bucket]) => ({
      league, hits: bucket.hits, total: bucket.total,
      rate: bucket.total > 0 ? bucket.hits / bucket.total : 0,
    }))
    .filter((point) => point.total > 0)
    .sort((a, b) => b.total - a.total)
    .slice(0, limit);
}

export type ShadowRatePoint = {
  key: string;
  label: string;
  hits: number;
  total: number;
  rate: number;
};

const SHADOW_METRIC_LABELS: Record<string, string> = {
  shots: "Tiros", shots_on_target: "Tiros a puerta", corners: "Córners",
  yellow_cards: "Amarillas", red_cards: "Rojas",
};
const SHADOW_PERIOD_LABELS: Record<string, string> = {
  first_half: "1ª mitad", second_half: "2ª mitad", full_match: "completo",
};
const SHADOW_SIDE_LABELS: Record<string, string> = {
  home: "Local", away: "Visitante", total: "Ambos",
};

/**
 * Etiqueta genérica de una línea shadow, sin nombres de equipo.
 *
 * `shadowMarketLabel` en `lib/track-record.ts` traduce la misma clave a texto
 * legible, pero necesita `homeName`/`awayName` de un partido concreto. Esta
 * gráfica suma la misma línea a través de partidos distintos -equipos
 * distintos cada vez-, así que la etiqueta usa "Local"/"Visitante" en vez del
 * nombre real: es la única forma honesta de nombrar un agregado.
 *
 * Omite el periodo a propósito: el eje de una gráfica de barras horizontales
 * en una pantalla de teléfono no tiene espacio para "Local · Córners · 1ª
 * mitad" sin recortarse. El periodo completo sigue en el tooltip.
 */
function shadowAggregateLabel(key: string): string {
  const match = key.match(/^(home|away|total)_(.+)_over_[\d_]+$/);
  if (!match) return key.replaceAll("_", " ");
  const [, side, rest] = match;
  const period = Object.keys(SHADOW_PERIOD_LABELS).find(
    (candidate) => rest === candidate || rest.endsWith(`_${candidate}`));
  const metric = period && rest !== period ? rest.slice(0, -(period.length + 1)) : rest;
  const metricName = SHADOW_METRIC_LABELS[metric] ?? metric.replaceAll("_", " ");
  return `${SHADOW_SIDE_LABELS[side] ?? side} · ${metricName}`;
}

/**
 * Tasa de acierto por línea shadow, sólo para mostrar volumen relativo.
 *
 * Estas líneas no están promovidas (`experimental_not_promoted`): la gráfica
 * existe para "cuántas veces se evaluó cada una", no para sugerir que un 70%
 * sobre 5 partidos es una señal. Por eso se etiqueta como experimental en el
 * componente, nunca junto a los mercados oficiales.
 */
export function shadowMarketRateSeries(shadowMarkets: unknown, limit = 8): ShadowRatePoint[] {
  const block = record(shadowMarkets);
  return Object.entries(block)
    .map(([key, value]) => {
      const market = record(value);
      const hits = Number(market.hits ?? 0);
      const total = Number(market.total ?? 0);
      return { key, label: shadowAggregateLabel(key), hits, total, rate: total > 0 ? hits / total : 0 };
    })
    .filter((point) => point.total > 0)
    .sort((a, b) => b.total - a.total)
    .slice(0, limit);
}
