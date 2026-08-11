"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { FavoriteButton } from "@/components/favorite-button";
import { FixtureContext } from "@/components/fixture-context";
import { useAuth } from "@/components/providers";
import { api, countLabel, edgeLabel, percentage, probabilityWidth, queryString, record, type Catalog } from "@/lib/client-api";
import { EntityImage } from "@/components/entity-image";
import { PredictionAnalytics } from "@/components/prediction-analytics";
import { ProviderPredictor } from "@/components/provider-predictor";

const ProbabilityChart = dynamic(() => import("@/components/probability-chart"), { ssr: false });

const metricLabels: Record<string, string> = {
  shots: "Tiros", shots_on_target: "Tiros a puerta", corners: "Córners",
  yellow_cards: "Tarjetas amarillas", red_cards: "Tarjetas rojas", goals: "Goles",
};

type Props = {
  fixtureId: string;
  league: string;
  home: string;
  away: string;
  homeName: string;
  awayName: string;
  kickoff: string;
};

function catalogDate(kickoff: string): string {
  const date = new Date(kickoff);
  if (!Number.isFinite(date.getTime())) return "";
  return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`;
}

export function PredictionDetail(props: Props) {
  const { csrfToken } = useAuth();
  const identityRequired = !props.homeName || !props.awayName;
  const identity = useQuery({
    queryKey: ["prediction-identity", props.fixtureId, props.league, props.kickoff],
    queryFn: () => api<Catalog>(`/api/upcoming${queryString({ limit: 20, leagues: props.league, date: catalogDate(props.kickoff) })}`),
    enabled: Boolean(props.fixtureId && props.league && props.kickoff),
    staleTime: 60_000,
  });
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
  if (query.isLoading || (identityRequired && identity.isLoading)) return <StatePanel title="Calculando pre-match">Resolviendo equipos y aplicando el snapshot versionado anterior al kickoff.</StatePanel>;
  const payload = query.data ?? {};
  const fixture = record(payload.fixture);
  const catalogFixture = identity.data?.fixtures.find((row) => String(row.match_id) === props.fixtureId);
  const homeName = String(fixture.home_team_name || props.homeName || catalogFixture?.home_team_name || `Equipo ${props.home}`);
  const awayName = String(fixture.away_team_name || props.awayName || catalogFixture?.away_team_name || `Equipo ${props.away}`);
  const homeLogo = String(fixture.home_team_logo || catalogFixture?.home_team_logo || "");
  const awayLogo = String(fixture.away_team_logo || catalogFixture?.away_team_logo || "");
  const teamMarkets = record(payload.experimental_team_markets);
  const probabilities = record(teamMarkets.probabilities);
  const marketRows = Array.isArray(teamMarkets.user_market_view)
    ? teamMarkets.user_market_view.map(record)
    : [];
  const gridRows = Array.isArray(teamMarkets.bounded_market_grid_view)
    ? teamMarkets.bounded_market_grid_view.map(record)
    : [];
  const periods = [
    { key: "first_half", label: "Primer tiempo" },
    { key: "second_half", label: "Segundo tiempo" },
    { key: "full_match", label: "Partido completo" },
  ];
  const outcomeValues = [
    { name: homeName, value: Number(payload.probability_home ?? 0), color: "var(--mint)" },
    { name: "Empate", value: Number(payload.probability_draw ?? 0), color: "var(--signal)" },
    { name: awayName, value: Number(payload.probability_away ?? 0), color: "var(--muted)" },
  ];
  return (
    <>
      <PageHeader eyebrow={`${props.league} · PRE-MATCH`} title={`${homeName} vs ${awayName}`} action={
        <FavoriteButton entityType="fixture" entityId={props.fixtureId} label={`${homeName} vs ${awayName}`} metadata={{ league: props.league, kickoff: props.kickoff }} />
      } />
      <div className="stack">
        <article className="data-panel match-identity"><div><EntityImage source={homeLogo} label={homeName} size={58} /><strong>{homeName}</strong></div><span>VS</span><div><EntityImage source={awayLogo} label={awayName} size={58} /><strong>{awayName}</strong></div></article>
        <article className="model-card">
          <div className="model-card-header"><h3>Dixon-Coles + Kalman</h3><ShadowBadge official /></div>
          <div className="probability-grid" style={{ marginTop: 16 }}>
            <div className="probability"><span>{homeName}</span><strong>{percentage(payload.probability_home)}</strong></div>
            <div className="probability"><span>Empate</span><strong>{percentage(payload.probability_draw)}</strong></div>
            <div className="probability"><span>{awayName}</span><strong>{percentage(payload.probability_away)}</strong></div>
          </div>
          <ProbabilityChart values={outcomeValues} />
          <div className="probability-grid" style={{ marginTop: 8 }}>
            <div className="probability"><span>Over 2.5</span><strong>{percentage(payload.probability_over_2_5)}</strong></div>
            <div className="probability"><span>BTTS</span><strong>{percentage(payload.probability_btts)}</strong></div>
            <div className="probability"><span>Modelo</span><strong style={{ fontSize: ".72rem" }}>{String(payload.model ?? "—")}</strong></div>
          </div>
        </article>
        <ProviderPredictor eventId={props.fixtureId} league={props.league} scope="pre_match" homeName={homeName} awayName={awayName} />
        <article className="data-panel analytics-panel">
          <PredictionAnalytics
            homeName={homeName} awayName={awayName}
            probabilityHome={Number(payload.probability_home)} probabilityDraw={Number(payload.probability_draw)} probabilityAway={Number(payload.probability_away)}
            expectedHomeGoals={Number(payload.expected_home_goals)} expectedAwayGoals={Number(payload.expected_away_goals)}
            lambdaHome={Number(payload.lambda_home)} lambdaAway={Number(payload.lambda_away)}
          />
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
                    <div className="market-probability" key={`${period.key}-${String(row.metric)}-${String(row.team_side)}-${index}`}>
                      <div><span>{row.team_side === "home" ? homeName : awayName} · {metricLabels[String(row.metric)] ?? String(row.metric).replaceAll("_", " ")} · más de {String(row.line)}</span><strong>{percentage(row.probability)}</strong></div>
                      <i><b className={row.team_side === "home" ? "home" : "away"} style={{ width: probabilityWidth(row.probability) }} /></i>
                    </div>
                  ))}
                </section>
              );
            }) : Object.entries(probabilities).map(([key, value]) => (
              <div className="subscription-row" key={key}><span>{key.replaceAll("home", homeName).replaceAll("away", awayName).replaceAll("_", " ")}</span><strong>{percentage(value)}</strong></div>
            ))}
          </div>
        </article>
        {gridRows.length ? (
          <article className="model-card">
            <div className="model-card-header"><h3>Rejilla adaptativa por periodo</h3><ShadowBadge /></div>
            <p className="ladder-caption">Líneas centradas en el 50% según la distribución causal de cada equipo. Varían por partido y periodo.</p>
            <div className="stack" style={{ marginTop: 14 }}>
              {periods.map((period) => {
                const rows = gridRows.filter((row) => row.period === period.key);
                if (!rows.length) return null;
                return (
                  <section className="period-market" key={`grid-${period.key}`}>
                    <p className="eyebrow">{period.label}</p>
                    {rows.map((row, index) => {
                      const lines = Array.isArray(row.lines) ? row.lines.map(record) : [];
                      if (!lines.length) return null;
                      const side = row.team_side === "home" ? "home" : "away";
                      const teamName = row.team_side === "home" ? homeName : awayName;
                      const metric = metricLabels[String(row.metric)] ?? String(row.metric).replaceAll("_", " ");
                      return (
                        <div className="ladder-group" key={`${period.key}-${String(row.key)}-${index}`}>
                          <div className="ladder-head">
                            <span>{teamName} · {metric}</span>
                            <strong>μ {countLabel(row.expected_count)}</strong>
                          </div>
                          {lines.map((line, lineIndex) => (
                            <div className="market-probability" key={`${String(row.key)}-${String(line.line)}-${lineIndex}`}>
                              <div>
                                <span>Más de {String(line.line)} · Menos {percentage(line.under_probability)}</span>
                                <strong>{percentage(line.over_probability)}</strong>
                              </div>
                              <i><b className={side} style={{ width: probabilityWidth(line.over_probability) }} /></i>
                              <small className="ladder-edge">vs baseline {edgeLabel(line.over_probability, line.baseline_over_probability)}</small>
                            </div>
                          ))}
                        </div>
                      );
                    })}
                  </section>
                );
              })}
            </div>
          </article>
        ) : null}
        <div className="notice">Las probabilidades shadow son analíticas y no constituyen cuotas ni recomendación de apuesta.</div>
      </div>
    </>
  );
}
