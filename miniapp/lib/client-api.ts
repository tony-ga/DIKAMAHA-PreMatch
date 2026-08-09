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
};

export type Catalog = {
  fixtures: Fixture[];
  count: number;
  status: string;
  partial_failure_count?: number;
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
