"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { FavoriteButton } from "@/components/favorite-button";
import { FixtureContext } from "@/components/fixture-context";
import { useAuth } from "@/components/providers";
import { api, percentage, record } from "@/lib/client-api";

type Props = {
  fixtureId: string;
  league: string;
  home: string;
  away: string;
  kickoff: string;
};

export function PredictionDetail(props: Props) {
  const { csrfToken } = useAuth();
  const query = useQuery({
    queryKey: ["prediction", props.fixtureId],
    queryFn: () => api<Record<string, unknown>>("/api/predict/upcoming", {
      method: "POST",
      body: JSON.stringify({
        match_id: Number(props.fixtureId),
        league_slug: props.league,
        home_team_id: Number(props.home),
        away_team_id: Number(props.away),
        kickoff_ts: props.kickoff,
      }),
    }, csrfToken),
    enabled: Boolean(props.league && props.home && props.away && props.kickoff),
  });
  if (query.isError) return <StatePanel title="Predicción no disponible">El fixture no cumple historia, identidad o cutoff causal.</StatePanel>;
  if (query.isLoading) return <StatePanel title="Calculando pre-match">Aplicando el snapshot versionado anterior al kickoff.</StatePanel>;
  const payload = query.data ?? {};
  const fixture = record(payload.fixture);
  const teamMarkets = record(payload.experimental_team_markets);
  const probabilities = record(teamMarkets.probabilities);
  const marketRows = Array.isArray(teamMarkets.user_market_view)
    ? teamMarkets.user_market_view.map(record)
    : [];
  const periods = [
    { key: "first_half", label: "Primer tiempo" },
    { key: "second_half", label: "Segundo tiempo" },
    { key: "full_match", label: "Partido completo" },
  ];
  return (
    <>
      <PageHeader eyebrow={`${props.league} · PRE-MATCH`} title={`${String(fixture.home_team_name ?? `Local ${props.home}`)} vs ${String(fixture.away_team_name ?? `Visitante ${props.away}`)}`} action={
        <FavoriteButton entityType="fixture" entityId={props.fixtureId} label={`${String(fixture.home_team_name ?? `Local ${props.home}`)} vs ${String(fixture.away_team_name ?? `Visitante ${props.away}`)}`} metadata={{ league: props.league, kickoff: props.kickoff }} />
      } />
      <div className="stack">
        <article className="model-card">
          <div className="model-card-header"><h3>Dixon-Coles + Kalman</h3><ShadowBadge official /></div>
          <div className="probability-grid" style={{ marginTop: 16 }}>
            <div className="probability"><span>Local</span><strong>{percentage(payload.probability_home)}</strong></div>
            <div className="probability"><span>Empate</span><strong>{percentage(payload.probability_draw)}</strong></div>
            <div className="probability"><span>Visita</span><strong>{percentage(payload.probability_away)}</strong></div>
          </div>
          <div className="probability-grid" style={{ marginTop: 8 }}>
            <div className="probability"><span>Over 2.5</span><strong>{percentage(payload.probability_over_2_5)}</strong></div>
            <div className="probability"><span>BTTS</span><strong>{percentage(payload.probability_btts)}</strong></div>
            <div className="probability"><span>Modelo</span><strong style={{ fontSize: ".72rem" }}>{String(payload.model ?? "—")}</strong></div>
          </div>
        </article>
        <FixtureContext league={props.league} eventId={props.fixtureId} />
        <article className="model-card">
          <div className="model-card-header"><h3>Mercados de equipo</h3><ShadowBadge /></div>
          <div className="stack" style={{ marginTop: 14 }}>
            {marketRows.length ? periods.map((period) => {
              const rows = marketRows.filter((row) => row.period === period.key);
              if (!rows.length) return null;
              return (
                <section className="period-market" key={period.key}>
                  <p className="eyebrow">{period.label}</p>
                  {rows.map((row, index) => (
                    <div className="subscription-row" key={`${period.key}-${String(row.metric)}-${String(row.team_side)}-${index}`}>
                      <span>{String(row.team_side === "home" ? "Local" : "Visita")} · {String(row.metric).replaceAll("_", " ")} over {String(row.line)}</span>
                      <strong>{percentage(row.probability)}</strong>
                    </div>
                  ))}
                </section>
              );
            }) : Object.entries(probabilities).map(([key, value]) => (
              <div className="subscription-row" key={key}><span>{key.replaceAll("_", " ")}</span><strong>{percentage(value)}</strong></div>
            ))}
          </div>
        </article>
        <div className="notice">Las probabilidades shadow son analíticas y no constituyen cuotas ni recomendación de apuesta.</div>
      </div>
    </>
  );
}
