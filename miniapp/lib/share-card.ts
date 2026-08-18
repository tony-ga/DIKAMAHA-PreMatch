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
 *
 * v2 reemplaza el contenido de v1 por completo: v1 apilaba nueve filas por
 * periodo con la media y su rango, y no cabia legible. v2 es una matriz por
 * equipo -metrica x periodo- mas el ganador y ambos marcan. `share-store` no
 * sirve una tarjeta cuya version no sea la vigente, y `POST /api/share`
 * reconstruye en su sitio la que se quedo atras conservando su token, para que
 * un link ya repartido no muera.
 */
export const SHARE_CARD_VERSION = 2 as const;

/** Columnas de cada tabla, en orden. */
export const CARD_PERIODS: Array<[string, string]> = [
  ["first_half", "1ª MITAD"],
  ["second_half", "2ª MITAD"],
  ["full_match", "COMPLETO"],
];

/**
 * Filas de cada tabla, en orden fijo.
 *
 * `shots` es la semantica comercial de DEC-110 (incluye goles), la misma que
 * publica el canal.
 */
export const CARD_METRICS = ["corners", "shots", "yellow_cards"] as const;

/**
 * Banda de probabilidad que una celda puede publicar.
 *
 * Es el mismo criterio que ya usa `_recommendations`
 * (`src/team_count_market_runtime.py`) para elegir un escenario no trivial por
 * grupo: recorrer la escalera, considerar over y under de cada linea, quedarse
 * con las que caen dentro de una banda y publicar la mas alta. Alli la banda es
 * `[0.55, 0.80]`; aqui el techo baja a 0.75 por peticion explicita.
 *
 * El techo es lo que elimina las obviedades. "Mas de 0.5 corners" en un partido
 * completo ronda el 99% y no informa de nada; queda fuera por si solo, sin una
 * lista de casos prohibidos que habria que mantener a mano. El piso descarta el
 * extremo contrario, la linea centrada en el 50% que es un volado disfrazado de
 * prediccion.
 *
 * Una celda sin ningun candidato en la banda se publica vacia. Es el caso de
 * una metrica cuya distribucion esta tan concentrada que ninguna linea cae en
 * el rango util -media tarjeta por mitad, por ejemplo-: preferible un hueco a
 * rellenarlo con una cifra que no dice nada.
 */
export const CARD_BAND_MIN = 0.55;
export const CARD_BAND_MAX = 0.75;

export type ShareCardCell = {
  line: number;
  direction: "over" | "under";
  probability: number;
};

export type ShareCardRow = {
  metric: string;
  label: string;
  /** Una celda por periodo de `CARD_PERIODS`; `null` si no hay nada que decir. */
  cells: Array<ShareCardCell | null>;
};

export type ShareCardTeam = {
  name: string;
  /** PNG en data URI, incrustado al congelar. Vacio si no se pudo obtener. */
  logo: string;
  rows: ShareCardRow[];
};

export type ShareCard = {
  version: typeof SHARE_CARD_VERSION;
  leagueSlug: string;
  kickoffTs: string;
  home: ShareCardTeam;
  away: ShareCardTeam;
  /** Sólo el resultado más probable, no los tres. */
  outcomeLabel: string;
  outcomeProbability: number;
  bttsLabel: "Sí" | "No";
  bttsProbability: number;
};

function probability(value: unknown): number {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.min(1, Math.max(0, number));
}

/**
 * Elige la linea mas decidida de un grupo dentro de la banda publicable.
 *
 * Recorre la escalera entera -no la rejilla acotada de
 * `bounded_market_grid_view`, cuyo tope de 9.5 deja fuera todo el rango util de
 * los tiros- y considera las dos direcciones de cada linea. `over_probability`
 * y `under_probability` son complementarias y monotonas por construccion: el
 * audit del propio runtime lo exige (`over_under_complementary`,
 * `over_under_monotonic`), asi que no hace falta recalcular ninguna.
 *
 * Desempate explicito -probabilidad, luego linea mas baja, luego `over`- para
 * que dos congelaciones del mismo partido no puedan diferir por el orden en que
 * llegaron los datos.
 */
export function bandCell(ladder: unknown): ShareCardCell | null {
  const candidates: ShareCardCell[] = [];
  for (const entry of Array.isArray(ladder) ? ladder : []) {
    const row = record(entry);
    const line = Number(row.line);
    if (!Number.isFinite(line)) continue;
    const over = probability(row.over_probability);
    const under = Number.isFinite(Number(row.under_probability))
      ? probability(row.under_probability) : 1 - over;
    candidates.push({ line, direction: "over", probability: over });
    candidates.push({ line, direction: "under", probability: under });
  }
  const eligible = candidates.filter(
    (item) => item.probability >= CARD_BAND_MIN
      && item.probability <= CARD_BAND_MAX);
  if (!eligible.length) return null;
  return eligible.reduce((best, item) => {
    if (item.probability !== best.probability) {
      return item.probability > best.probability ? item : best;
    }
    if (item.line !== best.line) return item.line < best.line ? item : best;
    return item.direction === "over" ? item : best;
  });
}

/**
 * Construye la matriz metrica x periodo de un equipo.
 *
 * Lee el lado `home` o `away` de `distributional_market_view`, que publica los
 * 18 grupos por equipo (3 metricas x 3 periodos x 2 lados, `_distributional_view`
 * en `src/team_count_market_runtime.py`). La version anterior de la tarjeta
 * usaba solo el lado `total`, que no permite una tabla por equipo.
 */
export function teamRows(teamMarkets: unknown, side: "home" | "away"): ShareCardRow[] {
  const view = record(teamMarkets).distributional_market_view;
  const groups = (Array.isArray(view) ? view.map(record) : [])
    .filter((group) => String(group.team_side) === side);
  return CARD_METRICS.map((metric) => ({
    metric,
    label: metricLabel(metric),
    cells: CARD_PERIODS.map(([period]) => {
      const group = groups.find(
        (row) => String(row.period) === period && String(row.metric) === metric);
      return group ? bandCell(group.ladder) : null;
    }),
  }));
}

/** Etiqueta y probabilidad del resultado 1X2 mas probable. */
export function headlineOutcome(
  homeName: string, awayName: string, source: Record<string, unknown>,
): { label: string; probability: number } {
  const options = [
    { label: homeName, probability: probability(source.probability_home) },
    { label: "Empate", probability: probability(source.probability_draw) },
    { label: awayName, probability: probability(source.probability_away) },
  ];
  return options.reduce((best, option) => (
    option.probability > best.probability ? option : best));
}

/**
 * Construye la tarjeta desde la respuesta cruda de `/v1/predict/upcoming`.
 *
 * No recalcula ninguna probabilidad: toma exactamente las que el modelo
 * devolvio, igual que hace la pantalla de detalle. Los logos llegan ya
 * resueltos como data URI porque descargarlos es una operacion de red que no
 * pertenece a una funcion pura -ver `lib/share-logo.ts`-.
 */
export function buildShareCard(
  payload: unknown,
  identity: {
    leagueSlug: string;
    homeName: string;
    awayName: string;
    kickoffTs: string;
    homeLogo?: string;
    awayLogo?: string;
  },
): ShareCard {
  const source = record(payload);
  const fixture = record(source.fixture);
  const homeName = String(fixture.home_team_name || identity.homeName || "Local");
  const awayName = String(fixture.away_team_name || identity.awayName || "Visitante");
  const markets = source.experimental_team_markets;
  const outcome = headlineOutcome(homeName, awayName, source);
  const btts = probability(source.probability_btts);
  return {
    version: SHARE_CARD_VERSION,
    leagueSlug: identity.leagueSlug,
    kickoffTs: identity.kickoffTs,
    home: {
      name: homeName, logo: identity.homeLogo ?? "",
      rows: teamRows(markets, "home"),
    },
    away: {
      name: awayName, logo: identity.awayLogo ?? "",
      rows: teamRows(markets, "away"),
    },
    outcomeLabel: outcome.label,
    outcomeProbability: outcome.probability,
    // Se publica el lado probable, no siempre el "Sí": "Ambos marcan: No, 62%"
    // es la misma afirmacion que "Sí, 38%" y se lee sin tener que invertirla.
    bttsLabel: btts >= 0.5 ? "Sí" : "No",
    bttsProbability: btts >= 0.5 ? btts : 1 - btts,
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
 * encima. Recortar aqui, en la capa de presentacion, deja el nombre completo
 * intacto en la tarjeta guardada.
 */
export function clip(value: string, limit: number): string {
  const clean = value.trim();
  return clean.length <= limit ? clean : `${clean.slice(0, limit - 1).trimEnd()}…`;
}

/** Iniciales de un equipo, para cuando no hay escudo que pintar. */
export function initials(label: string): string {
  const parts = label.split(/\s+/).filter(Boolean).slice(0, 2);
  return parts.map((part) => part[0]).join("").toUpperCase() || "?";
}

/** Clave estable del partido, la misma que usa el publicador del canal. */
export function shareFixtureKey(leagueSlug: string, matchId: number): string {
  return `${leagueSlug}:${matchId}`;
}
