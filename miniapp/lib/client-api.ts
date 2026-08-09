export async function api<T>(
  path: string,
  options: RequestInit = {},
  csrfToken?: string,
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
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

export function layerMarkets(value: unknown): Record<string, unknown> {
  const layer = record(value);
  const markets = record(layer.markets);
  return Object.keys(markets).length ? markets : layer;
}

export function queryString(values: Record<string, string | number | undefined>): string {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && String(value).trim()) parameters.set(key, String(value));
  }
  const query = parameters.toString();
  return query ? `?${query}` : "";
}
