import { metricLabel } from "@/lib/audited-ladder";
import { record } from "@/lib/client-api";

/**
 * Tarjeta compartible: la predicción pre-match reducida a lo que cabe legible
 * en una imagen.
 *
 * Se persiste tal cual, ya resuelta, en vez de guardar sólo el `fixture_key` y
 * recalcular al pintar: el link comparte "lo que el modelo dijo antes del
 * kickoff", y una tarjeta que cambiara al reabrirla no seria eso. Congelarla
 * tambien deja la imagen fuera del camino critico del backend -servirla no
 * llama a `/v1/predict/upcoming`-, que importa cuando el link circula por
 * WhatsApp y cada vista previa dispara una peticion.
 */
export const SHARE_CARD_VERSION = 1 as const;

/** Periodos publicados, en el orden en que se leen en la tarjeta. */
const PERIOD_LABELS: Array<[string, string]> = [
  ["first_half", "Primera mitad"],
  ["second_half", "Segunda mitad"],
  ["full_match", "Partido completo"],
];

/**
 * Metricas que entran en la tarjeta, en orden fijo.
 *
 * `shots` es la semantica comercial de DEC-110 (incluye goles), la misma que
 * publica el canal.
 */
const CARD_METRICS = ["corners", "shots", "yellow_cards"] as const;

/**
 * Una fila de conteo: cuantos se esperan y entre que valores cae el 60%.
 *
 * No es una linea over/under, y el cambio no es cosmetico. Una linea unica no
 * puede ser informativa y decidida a la vez: cerca del centro de la
 * distribucion es ~50% por definicion, y lejos del centro es ~certeza. La
 * primera version publicaba la linea central de `bounded_market_grid_view`
 * -centrada en P(over)=50% por `_centered_lines`- y las nueve filas eran
 * volados; elegir en su lugar la mas alejada del 50% degenera en la contraria,
 * una fila que siempre roza el 97% escogida precisamente por ser casi segura.
 * El tope de 9.5 de la rejilla (`VISIBLE_LINE_MAX`) agravaba las dos: los
 * tiros superan esa linea en cualquier periodo, asi que la tarjeta repetia
 * "Tiros - Mas de 8.5" en las tres mitades con 77%, 87% y 100%, que no dice
 * nada del partido, solo que dura mas que una mitad.
 *
 * La media con su rango central si distingue los periodos por construccion
 * -4.7 en la primera mitad, 5.6 en la segunda, 10.3 en el partido- y no puede
 * degenerar en ninguno de los dos extremos.
 */
export type ShareCardLine = {
  metric: string;
  label: string;
  /** Media de la distribucion congelada. */
  expected: number;
  /** Cuantiles 20% y 80%, la misma definicion que `_central_interval`. */
  intervalLow: number;
  intervalHigh: number;
};

export type ShareCardPeriod = {
  period: string;
  label: string;
  lines: ShareCardLine[];
};

export type ShareCard = {
  version: typeof SHARE_CARD_VERSION;
  leagueSlug: string;
  homeName: string;
  awayName: string;
  kickoffTs: string;
  probabilityHome: number;
  probabilityDraw: number;
  probabilityAway: number;
  probabilityOver25: number;
  probabilityBtts: number;
  headlineLabel: string;
  headlineProbability: number;
  periods: ShareCardPeriod[];
};

function probability(value: unknown): number {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(1, Math.max(0, number));
}

/** Menor conteo cuya CDF alcanza el cuantil, igual que `_quantile`. */
function quantile(mass: Array<{ count: number; probability: number }>, target: number): number {
  let cumulative = 0;
  for (const point of mass) {
    cumulative += point.probability;
    if (cumulative >= target) return point.count;
  }
  return mass.length ? mass[mass.length - 1].count : 0;
}

/**
 * Resume la distribucion congelada de un grupo en media y rango central.
 *
 * Lee `probability_mass` de `distributional_market_view`, no la rejilla
 * acotada: la rejilla recorta sus lineas a 1.5-9.5 y para los tiros eso deja
 * fuera todo el rango util. La PMF no tiene ese tope.
 *
 * El intervalo son los cuantiles 20% y 80%, exactamente la definicion de
 * `_central_interval` (`src/team_count_market_runtime.py`), para que la cifra
 * de la tarjeta y la que publica `global_market_view` en la aplicacion sean la
 * misma y no dos versiones de "rango central".
 */
function countSummary(group: Record<string, unknown>): Omit<ShareCardLine, "metric" | "label"> | null {
  const mass = (Array.isArray(group.probability_mass) ? group.probability_mass : [])
    .map(record)
    .filter((point) => Number.isFinite(Number(point.count))
      && Number.isFinite(Number(point.probability)))
    .map((point) => ({
      count: Number(point.count), probability: Number(point.probability),
    }))
    .sort((a, b) => a.count - b.count);
  if (!mass.length) return null;
  const expected = Number(group.expected_count);
  if (!Number.isFinite(expected)) return null;
  return {
    expected,
    intervalLow: quantile(mass, 0.2),
    intervalHigh: quantile(mass, 0.8),
  };
}

/**
 * Reduce la rejilla congelada a tres filas por periodo.
 *
 * Se publica unicamente el lado `total` -la suma de ambos equipos-. La rejilla
 * completa son hasta 27 grupos (3 lados x 3 metricas x 3 periodos) y ninguna
 * imagen los sostiene legibles; el total es ademas el unico de los tres lados
 * que se entiende sin saber cual equipo es local. Un grupo sin lado `total`
 * para esa metrica y periodo simplemente no aparece, en vez de sustituirse por
 * otro lado que diria algo distinto de lo que su etiqueta promete.
 */
export function shareCardPeriods(teamMarkets: unknown): ShareCardPeriod[] {
  const view = record(teamMarkets).distributional_market_view;
  const groups = (Array.isArray(view) ? view.map(record) : [])
    .filter((group) => String(group.team_side) === "total");
  const periods: ShareCardPeriod[] = [];
  for (const [period, label] of PERIOD_LABELS) {
    const lines: ShareCardLine[] = [];
    for (const metric of CARD_METRICS) {
      const group = groups.find(
        (row) => String(row.period) === period && String(row.metric) === metric);
      const summary = group ? countSummary(group) : null;
      if (summary) {
        lines.push({ ...summary, metric, label: metricLabel(metric) });
      }
    }
    if (lines.length) periods.push({ period, label, lines });
  }
  return periods;
}

/** Etiqueta y probabilidad del resultado 1X2 mas probable. */
export function headlineOutcome(
  card: Pick<ShareCard,
    "homeName" | "awayName" | "probabilityHome" | "probabilityDraw" | "probabilityAway">,
): { label: string; probability: number } {
  const options: Array<{ label: string; probability: number }> = [
    { label: card.homeName, probability: card.probabilityHome },
    { label: "Empate", probability: card.probabilityDraw },
    { label: card.awayName, probability: card.probabilityAway },
  ];
  return options.reduce((best, option) => (
    option.probability > best.probability ? option : best));
}

/**
 * Construye la tarjeta desde la respuesta cruda de `/v1/predict/upcoming`.
 *
 * No recalcula ninguna probabilidad: toma exactamente las que el modelo
 * devolvio, igual que hace la pantalla de detalle.
 */
export function buildShareCard(
  payload: unknown,
  identity: {
    leagueSlug: string;
    homeName: string;
    awayName: string;
    kickoffTs: string;
  },
): ShareCard {
  const source = record(payload);
  const fixture = record(source.fixture);
  const base = {
    version: SHARE_CARD_VERSION,
    leagueSlug: identity.leagueSlug,
    homeName: String(fixture.home_team_name || identity.homeName || "Local"),
    awayName: String(fixture.away_team_name || identity.awayName || "Visitante"),
    kickoffTs: identity.kickoffTs,
    probabilityHome: probability(source.probability_home),
    probabilityDraw: probability(source.probability_draw),
    probabilityAway: probability(source.probability_away),
    probabilityOver25: probability(source.probability_over_2_5),
    probabilityBtts: probability(source.probability_btts),
  };
  const headline = headlineOutcome(base);
  return {
    ...base,
    headlineLabel: headline.label,
    headlineProbability: headline.probability,
    periods: shareCardPeriods(source.experimental_team_markets),
  };
}

/**
 * Token de un link compartido: 32 bytes de `crypto.getRandomValues` en
 * base64url.
 *
 * El link es la unica credencial de una tarjeta publica, asi que la entropia
 * es la que la protege: 256 bits no se adivinan ni se enumeran. No se deriva
 * del `fixture_key` a proposito -un token derivado seria calculable por
 * cualquiera que conozca el partido, y entonces "no listado" no significaria
 * nada-.
 */
export function shareToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

/** Acepta unicamente la forma que `shareToken` produce. */
export function isShareToken(value: string): boolean {
  return /^[A-Za-z0-9_-]{43}$/.test(value);
}

/**
 * Acorta un nombre de equipo para que ocupe siempre una línea.
 *
 * La altura de la tarjeta es fija y Satori no recorta lo que se sale: pinta
 * encima. Un par de nombres largos -"Wolverhampton Wanderers" contra
 * "Brighton & Hove Albion"- añadía dos líneas entre el título y las columnas
 * 1X2, y el pie acababa solapando la última fila de mercados. Recortar aquí,
 * en la capa de presentación, deja el nombre completo intacto en la tarjeta
 * guardada.
 */
export function clip(value: string, limit: number): string {
  const clean = value.trim();
  return clean.length <= limit ? clean : `${clean.slice(0, limit - 1).trimEnd()}…`;
}

/** Clave estable del partido, la misma que usa el publicador del canal. */
export function shareFixtureKey(leagueSlug: string, matchId: number): string {
  return `${leagueSlug}:${matchId}`;
}
