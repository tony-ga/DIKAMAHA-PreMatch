"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { AuditedLadder } from "@/components/audited-ladder";
import { FavoriteButton } from "@/components/favorite-button";
import { FixtureContext } from "@/components/fixture-context";
import { LoadingProgress } from "@/components/loading-progress";
import { MarketGrid } from "@/components/market-grid";
import { PickBuilderBar, PickToggle } from "@/components/pick-toggle";
import { PremiumUpsell } from "@/components/premium-gate";
import { useAuth } from "@/components/providers";
import { api, percentage, queryString, record, unavailableReason, type Catalog } from "@/lib/client-api";
import { EntityImage } from "@/components/entity-image";
import { PredictionAnalytics } from "@/components/prediction-analytics";
import { ProviderPredictor } from "@/components/provider-predictor";
import { SharePrediction } from "@/components/share-prediction";
import type { GoalMarketKey, MatchRef } from "@/lib/pick-builder";
import { goalContextFrom, goalPick } from "@/lib/pick-sources";

const ProbabilityChart = dynamic(() => import("@/components/probability-chart"), { ssr: false });

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
    // `csrfToken` entra en la condición porque esta lectura viaja por POST y
    // el proxy la rechaza sin token. Durante el arranque optimista la interfaz
    // ya está pintada pero `/api/session/me` aún no ha respondido; lanzarla
    // antes sólo cachearía un 403 que el usuario vería como fallo real.
    enabled: Boolean(csrfToken && props.league && props.home && props.away && props.kickoff),
  });
  if (query.isError) {
    const code = query.error instanceof Error ? query.error.message : "";
    // El cupo agotado no es un fallo del servicio: es el plan gratuito
    // funcionando. Mostrarlo como "servicio no disponible" haría creer al
    // usuario que algo está roto.
    if (code === "prediction_quota_exhausted") {
      return (
        <PremiumUpsell
          headline="Has usado tus 3 predicciones de hoy"
          detail="Vuelves a tener 3 mañana. Con Premium no hay límite."
        />
      );
    }
    if (code === "premium_required") {
      return <PremiumUpsell headline="Esta función es parte de Premium" />;
    }
    const { title, detail } = unavailableReason(query.error);
    return <StatePanel title={title}>{detail}</StatePanel>;
  }
  if (query.isLoading || (identityRequired && identity.isLoading)) return <LoadingProgress title="Calculando pre-match" />;
  const payload = query.data ?? {};
  const fixture = record(payload.fixture);
  const catalogFixture = identity.data?.fixtures.find((row) => String(row.match_id) === props.fixtureId);
  const homeName = String(fixture.home_team_name || props.homeName || catalogFixture?.home_team_name || `Equipo ${props.home}`);
  const awayName = String(fixture.away_team_name || props.awayName || catalogFixture?.away_team_name || `Equipo ${props.away}`);
  const homeLogo = String(fixture.home_team_logo || catalogFixture?.home_team_logo || "");
  const awayLogo = String(fixture.away_team_logo || catalogFixture?.away_team_logo || "");
  const teamMarkets = record(payload.experimental_team_markets);
  // Identidad del partido que viaja con cada selección del constructor: sin
  // ella, dos mercados de partidos distintos no se podrían separar en el menú.
  const match: MatchRef = {
    matchId: props.fixtureId, league: props.league,
    homeTeamId: props.home, awayTeamId: props.away, kickoff: props.kickoff,
    homeName, awayName, homeLogo, awayLogo,
  };
  // `null` cuando el payload no trae lambdas usables. Entonces no se ofrece el
  // "+" en los mercados de gol: es preferible no poder elegirlos a combinarlos
  // con un supuesto de independencia que la matriz de marcadores desmiente.
  const goalContext = goalContextFrom(payload);
  const goalToggle = (market: GoalMarketKey, caption?: string) => (
    <PickToggle caption={caption} pick={goalContext ? goalPick(match, goalContext, market) : null} />
  );
  const auditedRows = teamMarkets.audited_market_ladder_view;
  const gridRows = teamMarkets.bounded_market_grid_view;
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
            <div className="probability"><span>{homeName}</span><strong>{percentage(payload.probability_home)}</strong>{goalToggle("home")}</div>
            <div className="probability"><span>Empate</span><strong>{percentage(payload.probability_draw)}</strong>{goalToggle("draw")}</div>
            <div className="probability"><span>{awayName}</span><strong>{percentage(payload.probability_away)}</strong>{goalToggle("away")}</div>
          </div>
          <ProbabilityChart values={outcomeValues} />
          <div className="probability-grid" style={{ marginTop: 8 }}>
            <div className="probability">
              <span>Over 2.5</span><strong>{percentage(payload.probability_over_2_5)}</strong>
              <span className="pick-toggle-row">{goalToggle("over_2_5", "Más")}{goalToggle("under_2_5", "Menos")}</span>
            </div>
            <div className="probability">
              <span>BTTS</span><strong>{percentage(payload.probability_btts)}</strong>
              <span className="pick-toggle-row">{goalToggle("btts_yes", "Sí")}{goalToggle("btts_no", "No")}</span>
            </div>
            <div className="probability"><span>Modelo</span><strong style={{ fontSize: ".72rem" }}>{String(payload.model ?? "—")}</strong></div>
          </div>
          <SharePrediction
            matchId={props.fixtureId} leagueSlug={props.league}
            homeTeamId={props.home} awayTeamId={props.away}
            kickoffTs={props.kickoff} homeName={homeName} awayName={awayName}
            homeLogo={homeLogo} awayLogo={awayLogo}
          />
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
        <AuditedLadder rows={auditedRows} homeName={homeName} awayName={awayName} match={match} />
        <MarketGrid rows={gridRows} homeName={homeName} awayName={awayName} match={match} />
        <div className="notice">Las probabilidades shadow son analíticas y no constituyen cuotas ni recomendación de apuesta.</div>
      </div>
      <PickBuilderBar />
    </>
  );
}
