"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { FavoriteButton } from "@/components/favorite-button";
import { api, countLabel, edgeLabel, percentage, probabilityWidth, record, unavailableReason } from "@/lib/client-api";
import { useAuth } from "@/components/providers";
import { EntityImage } from "@/components/entity-image";
import { ProviderPredictor } from "@/components/provider-predictor";

const ProbabilityChart = dynamic(() => import("@/components/probability-chart"), { ssr: false });
const PressureChart = dynamic(() => import("@/components/pressure-chart"), { ssr: false });

type Props = { fixtureId: string; league: string };

const LIVE_REFRESH_MS = 15_000;

// Rechazos de contrato: el snapshot histórico o el fixture no van a cambiar
// entre un refresco y el siguiente, así que seguir sondeando cada 15s sólo
// repite el mismo 422 (visto en producción: ráfagas de una docena de intentos
// para una Supercopa con historial insuficiente). `upstream_unavailable` y
// errores de red sí son transitorios y conservan el auto-refresco.
const TERMINAL_LIVE_ERRORS = new Set([
  "league_history_below_minimum",
  "unsupported_league",
  "live_fixture_not_active",
]);

const STAT_ROWS = [
  ["goals", "Goles"],
  ["shots", "Tiros"],
  ["shots_on_target", "A puerta"],
  ["shots_off_target", "Fuera"],
  ["shots_blocked", "Bloqueados"],
  ["corners", "Córners"],
  ["yellow_cards", "Tarjetas amarillas"],
  ["red_cards", "Tarjetas rojas"],
  ["fouls", "Faltas"],
  ["offsides", "Fueras de juego"],
  ["saves", "Atajadas"],
  ["substitutions", "Cambios"],
] as const;

const ACTION_LABELS: Record<string, [string, string]> = {
  goal: ["⚽", "Gol"], penalty_scored: ["⚽", "Penal anotado"],
  yellow: ["▰", "Tarjeta amarilla"], red: ["▰", "Tarjeta roja"],
  corner: ["◩", "Córner"], foul: ["×", "Falta"],
  shot_on_target: ["◎", "Tiro a puerta"], shot_off_target: ["○", "Tiro fuera"],
  shot_blocked: ["◈", "Tiro bloqueado"], substitution: ["⇄", "Cambio"],
  penalty_awarded: ["●", "Penal señalado"], save: ["◆", "Atajada"],
  offside: ["⚑", "Fuera de juego"], shot_hit_woodwork: ["▥", "Tiro al poste"],
};

function probabilities(markets: Record<string, unknown>, homeName: string, awayName: string) {
  return [
    { name: homeName, value: Number(markets.probability_home ?? 0) },
    { name: "Empate", value: Number(markets.probability_draw ?? 0) },
    { name: awayName, value: Number(markets.probability_away ?? 0) },
  ].filter((row) => Number.isFinite(row.value));
}

function OfficialLivePrediction({ value, engine, teamMarkets, homeName, awayName }: { value: unknown; engine: unknown; teamMarkets: unknown; homeName: string; awayName: string }) {
  const official = record(value);
  const layers = record(engine);
  const markets = record(official.markets);
  const periods = record(official.periods);
  const intensities = record(official.remaining_intensities);
  const confidence = record(official.confidence);
  const fallback = record(official.fallback);
  const exactScores = Array.isArray(official.exact_score) ? official.exact_score.map(record).slice(0, 5) : [];
  const horizon = record(official.goal_horizons);
  const nextEvent = record(official.next_event);
  const nextEvents = Object.entries(record(nextEvent.probabilities)).map(([name, probability]) => ({ name: name.replace(":", " · "), probability: Number(probability) })).filter((row) => Number.isFinite(row.probability)).sort((left, right) => right.probability - left.probability).slice(0, 3);
  const audit = record(layers.audit);
  const monteCarlo = record(layers.monte_carlo_diagnostic);
  const periodRows = [
    ["Primer tiempo", record(record(periods.first_half).markets)],
    ["Segundo tiempo", record(record(periods.second_half).markets)],
    ["Tiempo completo", record(record(periods.full_time).markets)],
  ] as const;

  if (!Object.keys(official).length) {
    return <StatePanel title="Motor live no disponible">La API no publicó el contrato oficial para este snapshot. Se conserva el fallback operativo.</StatePanel>;
  }
  return (
    <>
      <article className="model-card official-live-card">
        <div className="model-card-header"><div><p className="eyebrow">SALIDA OFICIAL DIKAMAHA</p><h3>Motor probabilístico in-live v1</h3></div><ShadowBadge official /></div>
        <p>Poisson dinámico, CTMC, peligro reciente, Elo live y residual Hawkes compuestos sobre intensidades.</p>
        <ProbabilityChart values={probabilities(markets, homeName, awayName)} />
        <div className="probability-grid">
          <div className="probability"><span>{homeName}</span><strong>{percentage(markets.probability_home)}</strong></div>
          <div className="probability"><span>Empate</span><strong>{percentage(markets.probability_draw)}</strong></div>
          <div className="probability"><span>{awayName}</span><strong>{percentage(markets.probability_away)}</strong></div>
        </div>
        <div className="live-engine-metrics">
          <div><span>λ restante {homeName}</span><strong>{Number(intensities.home ?? 0).toFixed(2)}</strong></div>
          <div><span>λ restante {awayName}</span><strong>{Number(intensities.away ?? 0).toFixed(2)}</strong></div>
          <div><span>Gol próximos 5 min</span><strong>{percentage(record(horizon.next_5m).probability_any_goal)}</strong></div>
          <div><span>Gol próximos 10 min</span><strong>{percentage(record(horizon.next_10m).probability_any_goal)}</strong></div>
        </div>
        {/* El contrato de fallback (`_official_markov_fallback`) devuelve
            `periods: {}`, así que pintar la tabla igual daba nueve guiones
            sin explicar por qué. La causa ya viaja en `fallback.reason`. */}
        {Object.keys(periods).length ? (
          <div className="live-period-table">
            {periodRows.map(([label, periodMarkets]) => <div key={label}><strong>{label}</strong><span>1 {percentage(periodMarkets.probability_home)}</span><span>X {percentage(periodMarkets.probability_draw)}</span><span>2 {percentage(periodMarkets.probability_away)}</span></div>)}
          </div>
        ) : (
          <p className="muted">Sin desglose por periodo en este snapshot: el motor cayó al contrato de compatibilidad{fallback.reason ? ` (${String(fallback.reason)})` : ""}.</p>
        )}
        {nextEvents.length ? <div className="next-event"><p className="eyebrow">PRÓXIMO EVENTO · {String(nextEvent.horizon_minutes ?? 5)} MIN</p>{nextEvents.map((event) => <div className="subscription-row" key={event.name}><span>{event.name}</span><strong>{percentage(event.probability)}</strong></div>)}<div className="subscription-row"><span>Sin evento</span><strong>{percentage(nextEvent.probability_no_event)}</strong></div></div> : null}
        {exactScores.length ? <div className="exact-score-strip"><span>Marcadores más probables</span>{exactScores.map((score) => <b key={`${score.score_home}-${score.score_away}`}>{String(score.score_home)}–{String(score.score_away)} <small>{percentage(score.probability)}</small></b>)}</div> : null}
        <div className="engine-health">
          <span className={audit.passed ? "health-ok" : "health-warn"}>{audit.passed ? "✓ Auditoría matemática aprobada" : "⚠ Auditoría en revisión"}</span>
          <span>Confianza {String(confidence.classification ?? confidence.status ?? "calculada")}</span>
          <span>Monte Carlo: {String(monteCarlo.status ?? "pendiente")} · {Number(monteCarlo.simulations ?? 0).toLocaleString("es-MX")} simulaciones</span>
        </div>
        {fallback.applied ? <div className="notice">Fallback aplicado: {String(fallback.source ?? "Markov Live")} · {String(fallback.reason ?? "calidad insuficiente")}</div> : null}
      </article>
      <LiveTeamMarkets value={teamMarkets} homeName={homeName} awayName={awayName} />
      <div className="component-grid">
        <ComponentCard title="Poisson dinámico" value={layers.dynamic_poisson} detailKeys={["integration", "remaining_seconds", "lambda_remaining_home", "lambda_remaining_away"]} />
        <ComponentCard title="CTMC" value={layers.ctmc} detailKeys={["dominant", "model"]} />
        <ComponentCard title="Hazard / Cox" value={layers.hazard} detailKeys={["model", "features_are_live_only"]} />
        <ComponentCard title="Elo dinámico" value={layers.dynamic_elo} detailKeys={["prior_difference", "live_difference", "shrinkage"]} />
        <ComponentCard title="Hawkes residual" value={layers.hawkes_residual} detailKeys={["model_version", "rho"]} residual />
      </div>
    </>
  );
}

const REMAINING_METRIC_LABELS: Record<string, string> = {
  corners: "Córners restantes", shots: "Tiros restantes",
};

function LiveTeamMarkets({ value, homeName, awayName }: { value: unknown; homeName: string; awayName: string }) {
  const block = record(value);
  const grid = Array.isArray(block.bounded_market_grid_view) ? block.bounded_market_grid_view.map(record) : [];
  const nextGoal = record(block.next_goal);
  const hasNextGoal = nextGoal.probability_no_more_goals !== undefined;
  if (!grid.length && !hasNextGoal) return null;
  return (
    <article className="model-card">
      <div className="model-card-header"><h3>Córners, tiros y próximo gol</h3><ShadowBadge /></div>
      <p className="ladder-caption">Proyección del tiempo restante desde el ritmo observado y el prior causal. Las líneas se recentran solas conforme avanza el partido. Salida experimental, no oficial.</p>
      {hasNextGoal ? (
        <div className="next-event">
          <p className="eyebrow">PRÓXIMO GOL · {countLabel(nextGoal.remaining_minutes)} MIN RESTANTES</p>
          <div className="subscription-row"><span>{homeName}</span><strong>{percentage(nextGoal.probability_home_next_goal)}</strong></div>
          <div className="subscription-row"><span>{awayName}</span><strong>{percentage(nextGoal.probability_away_next_goal)}</strong></div>
          <div className="subscription-row"><span>Sin más goles</span><strong>{percentage(nextGoal.probability_no_more_goals)}</strong></div>
        </div>
      ) : null}
      {grid.length ? (
        <div className="stack" style={{ marginTop: 14 }}>
          {grid.map((row, index) => {
            const lines = Array.isArray(row.lines) ? row.lines.map(record) : [];
            if (!lines.length) return null;
            const side = row.team_side === "home" ? "home" : "away";
            const teamName = row.team_side === "home" ? homeName : awayName;
            const metric = REMAINING_METRIC_LABELS[String(row.metric)] ?? String(row.metric).replaceAll("_", " ");
            return (
              <div className="ladder-group" key={String(row.key ?? index)}>
                <div className="ladder-head"><span>{teamName} · {metric}</span><strong>μ {countLabel(row.expected_remaining)}</strong></div>
                {lines.map((line, lineIndex) => (
                  <div className="market-probability" key={`${String(row.key)}-${String(line.line)}-${lineIndex}`}>
                    <div><span>Más de {String(line.line)} · Menos {percentage(line.under_probability)}</span><strong>{percentage(line.over_probability)}</strong></div>
                    <i><b className={side} style={{ width: probabilityWidth(line.over_probability) }} /></i>
                    <small className="ladder-edge">vs ritmo base {edgeLabel(line.over_probability, line.baseline_over_probability)}</small>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      ) : null}
    </article>
  );
}

function ComponentCard({ title, value, detailKeys, residual = false }: { title: string; value: unknown; detailKeys: string[]; residual?: boolean }) {
  const layer = record(value);
  const details = detailKeys.map((key) => [key.replaceAll("_", " "), layer[key]] as const).filter(([, value]) => value !== undefined);
  return <article className="model-card component-card"><div className="model-card-header"><h3>{title}</h3><span className={residual ? "mode-badge" : "mode-badge official"}>{residual ? "RESIDUAL" : "COMPONENTE"}</span></div><p>{residual ? "Memoria corta acotada; no publica una probabilidad competidora." : "Capa auditable integrada en la composición oficial."}</p>{details.map(([key, value]) => <div className="subscription-row" key={key}><span>{key}</span><strong>{typeof value === "number" ? value.toFixed(4) : String(value)}</strong></div>)}</article>;
}

function numberValue(row: Record<string, unknown>, key: string) {
  const value = Number(row[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function ObservedStatistics({ value, homeName, awayName }: { value: unknown; homeName: string; awayName: string }) {
  const statistics = record(value);
  const home = record(statistics.home);
  const away = record(statistics.away);
  // El backend sólo declara una métrica ausente cuando el boxscore -su
  // fuente autoritativa- llegó y la omitió. Sin esa lista, un 0 del
  // play-by-play y un dato que el proveedor nunca publicó se veían igual.
  const unavailable = new Set(
    Array.isArray(statistics.unavailable_metrics)
      ? statistics.unavailable_metrics.map(String)
      : [],
  );
  const fromBoxscore = statistics.source === "provider_boxscore_aggregate";
  return (
    <article className="data-panel live-statistics">
      <div className="panel-heading"><div><p className="eyebrow">DATOS DEL PARTIDO</p><h3>Acciones observadas</h3></div><span className="provider-chip">{fromBoxscore ? "BOXSCORE OFICIAL" : "PLAY-BY-PLAY"}</span></div>
      <div className="live-stat-header"><strong>{homeName}</strong><span>Comparativa</span><strong>{awayName}</strong></div>
      <div className="live-stat-list">
        {STAT_ROWS.map(([key, label]) => {
          const missing = unavailable.has(key);
          const homeValue = numberValue(home, key);
          const awayValue = numberValue(away, key);
          const total = Math.max(homeValue + awayValue, 1);
          return (
            <div className="live-stat-row" key={key}>
              <strong>{missing ? "—" : homeValue}</strong>
              <div><span>{label}</span>{missing ? <small className="muted">sin dato del proveedor</small> : <div className="live-stat-bars"><i><b style={{ width: `${(homeValue / total) * 100}%` }} /></i><i><b style={{ width: `${(awayValue / total) * 100}%` }} /></i></div>}</div>
              <strong>{missing ? "—" : awayValue}</strong>
            </div>
          );
        })}
      </div>
      <p className="live-source-note">{fromBoxscore ? "Conteos oficiales agregados del proveedor." : "Conteos derivados del flujo de jugadas del proveedor."} El marcador usa su valor oficial.</p>
    </article>
  );
}

function RecentActions({ value }: { value: unknown }) {
  const actions = Array.isArray(value) ? value.map(record) : [];
  return (
    <article className="data-panel">
      <div className="panel-heading"><div><p className="eyebrow">CRONOLOGÍA LIVE</p><h3>Acciones recientes</h3></div><span className="live-pill"><i /> EN VIVO</span></div>
      {actions.length ? <div className="live-action-list">{actions.slice(0, 12).map((action, index) => {
        const raw = String(action.event_type_raw ?? "");
        const canonical = String(action.event_type ?? "");
        const [icon, label] = ACTION_LABELS[canonical] ?? ACTION_LABELS[raw] ?? ["·", raw.replaceAll("_", " ") || "Acción"];
        return <div className={`live-action ${String(action.team_side ?? "unassigned")}`} key={String(action.event_id ?? index)}><span className="action-minute">{String(action.minute ?? "—")}&apos;</span><span className="action-icon">{icon}</span><div><strong>{label} · {String(action.team_name ?? "")}</strong><p>{String(action.text || "Evento registrado por el proveedor")}</p></div></div>;
      })}</div> : <p className="muted">Todavía no hay acciones relevantes publicadas por el proveedor.</p>}
    </article>
  );
}

export function LiveDetail({ fixtureId, league }: Props) {
  const { csrfToken } = useAuth();
  const query = useQuery({
    queryKey: ["live-prediction", fixtureId, league],
    queryFn: () => api<Record<string, unknown>>("/api/predict/live", {
      method: "POST",
      body: JSON.stringify({ league_slug: league, match_id: Number(fixtureId) }),
    }, csrfToken),
    // `csrfToken` entra en la condición porque esta lectura viaja por POST y
    // el proxy la rechaza sin token: durante el arranque optimista la interfaz
    // ya está pintada pero la sesión aún no se ha confirmado.
    enabled: Boolean(csrfToken && league && fixtureId),
    refetchInterval: (activeQuery) => {
      const code = activeQuery.state.error instanceof Error ? activeQuery.state.error.message : "";
      return TERMINAL_LIVE_ERRORS.has(code) ? false : LIVE_REFRESH_MS;
    },
    refetchOnWindowFocus: true,
  });
  const payload = query.data ?? {};
  const fixture = record(payload.fixture);
  const clockSeconds = Number(fixture.match_clock_seconds);
  const clock = Number.isFinite(clockSeconds)
    ? `${Math.floor(clockSeconds / 60)}:${String(Math.floor(clockSeconds % 60)).padStart(2, "0")}`
    : String(fixture.display_clock ?? "En vivo");
  const updatedAt = typeof fixture.source_fetched_at === "string"
    ? new Date(fixture.source_fetched_at).toLocaleTimeString("es-MX")
    : "—";
  const homeName = String(fixture.home_team_name ?? `Equipo ${String(fixture.home_team_id ?? "A")}`);
  const awayName = String(fixture.away_team_name ?? `Equipo ${String(fixture.away_team_id ?? "B")}`);
  if (query.isError) {
    const { title, detail } = unavailableReason(query.error);
    return <StatePanel title={title}>{detail}</StatePanel>;
  }
  return (
    <>
      <PageHeader eyebrow={`${league} · AUTO 15 S`} title={`${homeName} vs ${awayName}`} action={
        <div style={{ display: "flex", gap: 8 }}>
          <FavoriteButton entityType="fixture" entityId={fixtureId} label={`${homeName} vs ${awayName}`} metadata={{ league }} />
          <button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar predicción">↻</button>
        </div>
      } />
      {query.isLoading ? <StatePanel title="Reconstruyendo snapshot causal">La API prepara el prior pre-match y observa únicamente eventos ya ocurridos.</StatePanel> : (
        <div className="stack">
          <article className="data-panel live-scoreboard">
            <div className="live-auto-status"><span><i /> DATOS EN VIVO</span><span>Actualización automática cada 15 s</span></div>
            <div className="live-team-stage">
              <div><EntityImage source={String(fixture.home_team_logo || "")} label={homeName} size={82} /><strong>{homeName}</strong><small>LOCAL</small></div>
              <div className="live-score"><span>{String(fixture.score_home ?? fixture.home_score ?? 0)}</span><b>–</b><span>{String(fixture.score_away ?? fixture.away_score ?? 0)}</span></div>
              <div><EntityImage source={String(fixture.away_team_logo || "")} label={awayName} size={82} /><strong>{awayName}</strong><small>VISITANTE</small></div>
            </div>
            <div className="live-clock"><strong>{clock}</strong><span>{String(fixture.provider_status_detail ?? fixture.provider_status ?? "live")}</span><small>Sincronizado {updatedAt}</small></div>
          </article>
          <ObservedStatistics value={payload.observed_live_statistics} homeName={homeName} awayName={awayName} />
          <RecentActions value={payload.recent_actions} />
          <PressureChart value={payload.match_dynamics} homeName={homeName} awayName={awayName} />
          <div className="prediction-divider"><span>PREDICCIONES EN TIEMPO REAL</span></div>
          <OfficialLivePrediction value={payload.official_live_prediction} engine={payload.live_probability_engine} teamMarkets={payload.experimental_live_team_markets} homeName={homeName} awayName={awayName} />
          <div className="prediction-divider"><span>BENCHMARK EXTERNO · NO ES FEATURE DEL MODELO</span></div>
          <ProviderPredictor eventId={fixtureId} league={league} scope="live" homeName={homeName} awayName={awayName} />
          <div className="notice">Predictor y Pickcenter del proveedor se muestran sólo como referencia externa. Sus probabilidades y cuotas no alimentan el cálculo DIKAMAHA.</div>
        </div>
      )}
    </>
  );
}
