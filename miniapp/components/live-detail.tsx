"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { FavoriteButton } from "@/components/favorite-button";
import { api, layerMarkets, percentage, record } from "@/lib/client-api";
import { useAuth } from "@/components/providers";
import { EntityImage } from "@/components/entity-image";

const ProbabilityChart = dynamic(() => import("@/components/probability-chart"), { ssr: false });

type Props = { fixtureId: string; league: string };

const LIVE_REFRESH_MS = 10_000;

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

function Layer({ title, value, note, homeName, awayName }: { title: string; value: unknown; note: string; homeName: string; awayName: string }) {
  const layer = record(value);
  const markets = layerMarkets(layer);
  const values = probabilities(markets, homeName, awayName);
  const nextEvent = record(layer.next_event);
  const nextProbabilities = Object.entries(record(nextEvent.probabilities))
    .map(([name, probability]) => ({ name: name.replace(":", " · "), value: Number(probability) }))
    .filter((row) => Number.isFinite(row.value))
    .sort((left, right) => right.value - left.value)
    .slice(0, 3);
  return (
    <article className="model-card">
      <div className="model-card-header"><h3>{title}</h3><ShadowBadge /></div>
      <p>{note}</p>
      {values.length === 3 ? (
        <>
          <div className="probability-grid" style={{ marginTop: 14 }}>
            {values.map((row) => <div className="probability" key={row.name}><span>{row.name}</span><strong>{percentage(row.value)}</strong></div>)}
          </div>
          <ProbabilityChart values={values} />
          <div className="probability-grid">
            <div className="probability"><span>Over 2.5</span><strong>{percentage(markets.probability_over_2_5)}</strong></div>
            <div className="probability"><span>BTTS</span><strong>{percentage(markets.probability_btts)}</strong></div>
            <div className="probability"><span>Goles restantes</span><strong>{Number(layer.lambda_remaining_home ?? 0).toFixed(1)}–{Number(layer.lambda_remaining_away ?? 0).toFixed(1)}</strong></div>
          </div>
          {nextProbabilities.length ? (
            <div className="next-event">
              <p className="eyebrow">PRÓXIMO EVENTO · {String(nextEvent.horizon_minutes ?? 5)} MIN</p>
              {nextProbabilities.map((event) => <div className="subscription-row" key={event.name}><span>{event.name}</span><strong>{percentage(event.value)}</strong></div>)}
              <div className="subscription-row"><span>Sin evento</span><strong>{percentage(nextEvent.probability_no_event)}</strong></div>
            </div>
          ) : null}
        </>
      ) : <p className="notice" style={{ marginTop: 14 }}>Esta capa describe el residual y no publica probabilidades independientes.</p>}
    </article>
  );
}

function numberValue(row: Record<string, unknown>, key: string) {
  const value = Number(row[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function ObservedStatistics({ value, homeName, awayName }: { value: unknown; homeName: string; awayName: string }) {
  const statistics = record(value);
  const home = record(statistics.home);
  const away = record(statistics.away);
  return (
    <article className="data-panel live-statistics">
      <div className="panel-heading"><div><p className="eyebrow">DATOS DEL PARTIDO</p><h3>Acciones observadas</h3></div><span className="provider-chip">PLAY-BY-PLAY</span></div>
      <div className="live-stat-header"><strong>{homeName}</strong><span>Comparativa</span><strong>{awayName}</strong></div>
      <div className="live-stat-list">
        {STAT_ROWS.map(([key, label]) => {
          const homeValue = numberValue(home, key);
          const awayValue = numberValue(away, key);
          const total = Math.max(homeValue + awayValue, 1);
          return (
            <div className="live-stat-row" key={key}>
              <strong>{homeValue}</strong>
              <div><span>{label}</span><div className="live-stat-bars"><i><b style={{ width: `${(homeValue / total) * 100}%` }} /></i><i><b style={{ width: `${(awayValue / total) * 100}%` }} /></i></div></div>
              <strong>{awayValue}</strong>
            </div>
          );
        })}
      </div>
      <p className="live-source-note">Conteos derivados del flujo de jugadas del proveedor. El marcador usa su valor oficial.</p>
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
    enabled: Boolean(league && fixtureId),
    refetchInterval: LIVE_REFRESH_MS,
    refetchOnWindowFocus: true,
  });
  const payload = query.data ?? {};
  const fixture = record(payload.fixture);
  const admission = record(payload.hawkes_league_admission);
  const clockSeconds = Number(fixture.match_clock_seconds);
  const clock = Number.isFinite(clockSeconds)
    ? `${Math.floor(clockSeconds / 60)}:${String(Math.floor(clockSeconds % 60)).padStart(2, "0")}`
    : String(fixture.display_clock ?? "En vivo");
  const updatedAt = typeof fixture.source_fetched_at === "string"
    ? new Date(fixture.source_fetched_at).toLocaleTimeString("es-MX")
    : "—";
  const homeName = String(fixture.home_team_name ?? `Equipo ${String(fixture.home_team_id ?? "A")}`);
  const awayName = String(fixture.away_team_name ?? `Equipo ${String(fixture.away_team_id ?? "B")}`);
  if (query.isError) return <StatePanel title="Predicción live no disponible" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>El snapshot fue rechazado o el partido ya no está activo.</StatePanel>;
  return (
    <>
      <PageHeader eyebrow={`${league} · AUTO 10 S`} title={`${homeName} vs ${awayName}`} action={
        <div style={{ display: "flex", gap: 8 }}>
          <FavoriteButton entityType="fixture" entityId={fixtureId} label={`${homeName} vs ${awayName}`} metadata={{ league }} />
          <button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar predicción">↻</button>
        </div>
      } />
      {query.isLoading ? <StatePanel title="Reconstruyendo snapshot causal">La API prepara el prior pre-match y observa únicamente eventos ya ocurridos.</StatePanel> : (
        <div className="stack">
          <article className="data-panel live-scoreboard">
            <div className="live-auto-status"><span><i /> DATOS EN VIVO</span><span>Actualización automática cada 10 s</span></div>
            <div className="live-team-stage">
              <div><EntityImage source={String(fixture.home_team_logo || "")} label={homeName} size={82} /><strong>{homeName}</strong><small>LOCAL</small></div>
              <div className="live-score"><span>{String(fixture.score_home ?? fixture.home_score ?? 0)}</span><b>–</b><span>{String(fixture.score_away ?? fixture.away_score ?? 0)}</span></div>
              <div><EntityImage source={String(fixture.away_team_logo || "")} label={awayName} size={82} /><strong>{awayName}</strong><small>VISITANTE</small></div>
            </div>
            <div className="live-clock"><strong>{clock}</strong><span>{String(fixture.provider_status_detail ?? fixture.provider_status ?? "live")}</span><small>Sincronizado {updatedAt}</small></div>
          </article>
          <ObservedStatistics value={payload.observed_live_statistics} homeName={homeName} awayName={awayName} />
          <RecentActions value={payload.recent_actions} />
          <div className="prediction-divider"><span>PREDICCIONES EN TIEMPO REAL</span></div>
          <Layer title="Markov Live" value={payload.experimental_markov_live} note="Baseline universal: régimen, marcador y tiempo restante." homeName={homeName} awayName={awayName} />
          <Layer title="Hawkes residual" value={payload.experimental_hawkes_residual} note="Memoria corta complementaria; nunca sustituye a Markov." homeName={homeName} awayName={awayName} />
          <Layer title="Resultado combinado" value={payload.experimental_combined_live} note="Markov más residual Hawkes acotado en escala logarítmica." homeName={homeName} awayName={awayName} />
          <div className="notice">Hawkes {admission.admitted ? "está admitido para esta liga" : "usa fallback Markov exacto en esta liga"}. Toda la vista permanece experimental shadow.</div>
        </div>
      )}
    </>
  );
}
