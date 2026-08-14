export async function api<T>(
  path: string,
  options: RequestInit = {},
  csrfToken?: string,
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
      ...options.headers,
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload.error === "string" ? payload.error : "request_failed");
  }
  return payload as T;
}

export type Fixture = {
  match_id: number;
  league_slug: string;
  competition_id?: string;
  home_team_id: number | string;
  away_team_id: number | string;
  home_team_name?: string;
  away_team_name?: string;
  kickoff_ts: string;
  provider_status?: string;
  home_score?: number;
  away_score?: number;
  display_clock?: string;
  home_team_logo?: string | null;
  away_team_logo?: string | null;
};

export type Catalog = {
  fixtures: Fixture[];
  count: number;
  status: string;
  partial_failure_count?: number;
  league_count?: number;
  date_count?: number;
  truncated?: boolean;
  leagues_with_hidden_fixtures?: string[];
};

export type League = { slug: string; name: string };
export type ExplorerDate = { date: string; label: string };
export type Team = {
  id: string;
  name: string;
  abbreviation?: string;
  logo?: string | null;
  league_slug?: string;
};
export type HistoricalFixture = Fixture & {
  competition_id: string;
  status_detail?: string;
};
export type Player = {
  id: string;
  name: string;
  short_name?: string;
  jersey?: string;
  position?: string;
  age?: number | null;
  headshot?: string | null;
  statistics?: Array<{ name: string; label: string; value: string }>;
};

export function percentage(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "—";
}

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function probabilityWidth(value: unknown): string {
  const numeric = Number(value);
  return `${Math.max(0, Math.min(100, Number.isFinite(numeric) ? numeric * 100 : 0))}%`;
}

export function edgeLabel(model: unknown, baseline: unknown): string {
  const delta = Number(model) - Number(baseline);
  if (!Number.isFinite(delta)) return "—";
  const points = delta * 100;
  return `${points >= 0 ? "+" : "−"}${Math.abs(points).toFixed(1)} pp`;
}

export function countLabel(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(1) : "—";
}

export function layerMarkets(value: unknown): Record<string, unknown> {
  const layer = record(value);
  const markets = record(layer.markets);
  return Object.keys(markets).length ? markets : layer;
}

/**
 * Traduce el código de contrato de DIKAMAHA a una explicación concreta.
 *
 * Antes todo fallo mostraba "no cumple historia, identidad o cutoff causal",
 * que mezcla tres causas distintas y no dice al usuario si el problema es suyo,
 * del partido o del servicio. El caso más frecuente es una competición sin
 * historial suficiente: la Supercopa de Europa, por ejemplo, tiene un único
 * partido en el snapshot y el motor exige ocho de la misma competición.
 * Compartida entre la vista pre-match y la vista live: ambas llaman al mismo
 * contrato de predicción y pueden recibir los mismos códigos de rechazo.
 */
export function unavailableReason(error: unknown): { title: string; detail: string } {
  const code = error instanceof Error ? error.message : "";
  if (code === "league_history_below_minimum") {
    return {
      title: "Sin historial suficiente en esta competición",
      detail:
        "El modelo sólo predice con historial causal de la misma competición, " +
        "y ésta no reúne los partidos mínimos en el snapshot. Ocurre en finales " +
        "a partido único y torneos recién añadidos. No es un fallo del " +
        "servicio: preferimos no publicar una probabilidad que no se sostiene.",
    };
  }
  if (code === "unsupported_league") {
    return {
      title: "Competición no soportada",
      detail: "Esta competición no forma parte del snapshot causal vigente.",
    };
  }
  if (code === "live_fixture_not_active") {
    return {
      title: "El partido ya no está en vivo",
      detail:
        "El proveedor de datos dejó de reportarlo como activo; probablemente " +
        "ya terminó. Vuelve al listado para ver los partidos en curso.",
    };
  }
  if (code === "upstream_unavailable") {
    return {
      title: "Servicio no disponible",
      detail: "No se pudo consultar el motor de predicción. Vuelve a intentarlo.",
    };
  }
  return {
    title: "Predicción no disponible",
    detail: "El fixture no cumple historia, identidad o cutoff causal.",
  };
}

export function queryString(values: Record<string, string | number | undefined>): string {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && String(value).trim()) parameters.set(key, String(value));
  }
  const query = parameters.toString();
  return query ? `?${query}` : "";
}
