"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { FixtureContext } from "@/components/fixture-context";
import { PageHeader, SegmentedControl, StatePanel } from "@/components/ui";
import { api, queryString, record } from "@/lib/client-api";
import { EntityImage } from "@/components/entity-image";

type Props = { fixtureId: string; competitionId: string; league: string; home: string; away: string };
type Play = { id: string; type: string; label: string; clock: string; period: number; text: string };

const metricLabels: Record<string, string> = {
  goals: "Goles", shots: "Tiros", shots_on_target: "A puerta",
  corners: "Córners", yellow_cards: "Amarillas", red_cards: "Rojas",
  fouls: "Faltas", offsides: "Fuera de juego", saves: "Atajadas",
  substitutions: "Cambios",
};
const statisticKeys = Object.keys(metricLabels);

function eventIcon(type: string): string {
  if (["goal", "penalty---scored", "own-goal"].includes(type)) return "⚽";
  if (type === "yellow-card") return "🟨";
  if (type === "red-card") return "🟥";
  if (type === "substitution") return "↔";
  if (type === "corner-awarded") return "⚑";
  if (type.startsWith("shot")) return "◎";
  return "•";
}

export function HistoricalMatchDetail(props: Props) {
  const [scope, setScope] = useState("key");
  const [period, setPeriod] = useState("total");
  const [page, setPage] = useState(0);
  const enabled = Boolean(props.league && props.fixtureId && props.competitionId);
  const plays = useQuery({
    queryKey: ["match-plays", props.fixtureId, props.competitionId, props.league, scope],
    queryFn: () => api<{ plays: Play[]; count: number; raw_count: number }>(`/api/explorer/match/plays${queryString({
      league: props.league, match_id: props.fixtureId, competition_id: props.competitionId, scope,
    })}`),
    enabled,
  });
  const statistics = useQuery({
    queryKey: ["match-statistics", props.fixtureId, props.competitionId, props.league],
    queryFn: () => api<Record<string, unknown>>(`/api/explorer/match/statistics${queryString({
      league: props.league, match_id: props.fixtureId, competition_id: props.competitionId,
    })}`),
    enabled,
  });
  const rows = plays.data?.plays ?? [];
  const maximumPage = Math.max(0, Math.ceil(rows.length / 8) - 1);
  const pageRows = rows.slice(page * 8, page * 8 + 8);
  const statsPayload = statistics.data ?? {};
  const teams = record(statsPayload.teams);
  const homeTeam = record(teams.home);
  const awayTeam = record(teams.away);
  const periods = record(statsPayload.periods);
  const homeStats = record(record(periods.home)[period]);
  const awayStats = record(record(periods.away)[period]);
  if (!enabled) return <StatePanel title="Identidad incompleta">Regresa al Centro de partidos y selecciona el fixture nuevamente.</StatePanel>;
  return (
    <>
      <PageHeader eyebrow={`${props.league} · DATOS DEL PARTIDO`} title={`${props.home || String(homeTeam.name ?? "Local")} vs ${props.away || String(awayTeam.name ?? "Visitante")}`} />
      <div className="stack">
        <FixtureContext league={props.league} eventId={props.fixtureId} />
        <article className="data-panel">
          <div className="panel-heading"><div><p className="eyebrow">PLAY-BY-PLAY</p><h3>{plays.data?.count ?? 0} eventos visibles</h3></div><SegmentedControl label="Alcance de eventos" value={scope} onChange={(value) => { setScope(value); setPage(0); }} options={[{ value: "key", label: "Clave" }, { value: "all", label: "Todos" }]} /></div>
          {plays.isError ? <StatePanel title="Eventos no disponibles">No se pudo reconciliar el play-by-play.</StatePanel> : plays.isLoading ? <p className="muted">Cargando eventos…</p> : pageRows.length ? (
            <div className="timeline">
              {pageRows.map((play) => <article className="timeline-row" key={play.id || `${play.clock}-${play.text}`}><span>{eventIcon(play.type)}</span><div><small>{play.period === 1 ? "1T" : "2T"} · {play.clock || "—"}</small><strong>{play.label || "Evento"}</strong><p>{play.text || "Sin descripción publicada"}</p></div></article>)}
              <div className="pager"><button className="secondary-button" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>← Anterior</button><span>{page + 1}/{maximumPage + 1}</span><button className="secondary-button" disabled={page >= maximumPage} onClick={() => setPage((value) => Math.min(maximumPage, value + 1))}>Siguiente →</button></div>
            </div>
          ) : <p className="muted">No hay eventos publicados para este alcance.</p>}
        </article>
        <article className="data-panel">
          <div className="panel-heading"><div><p className="eyebrow">ESTADÍSTICAS RECONCILIADAS</p><h3>Comparación por periodo</h3></div><SegmentedControl label="Periodo estadístico" value={period} onChange={setPeriod} options={[{ value: "first_half", label: "1T" }, { value: "second_half", label: "2T" }, { value: "total", label: "Total" }]} /></div>
          {statistics.isError ? <StatePanel title="Estadísticas no disponibles">DIKAMAHA rechazó o no recibió datos reconciliables.</StatePanel> : statistics.isLoading ? <p className="muted">Cargando estadísticas…</p> : (
            <>
              <div className="stats-table" role="table" aria-label={`Estadísticas ${period}`}>
                <div className="stats-row header" role="row"><span>Métrica</span><strong><EntityImage source={String(homeTeam.logo || "")} label={String(homeTeam.name ?? "Local")} size={28} />{String(homeTeam.abbreviation ?? homeTeam.name ?? "LOC")}</strong><strong><EntityImage source={String(awayTeam.logo || "")} label={String(awayTeam.name ?? "Visitante")} size={28} />{String(awayTeam.abbreviation ?? awayTeam.name ?? "VIS")}</strong></div>
                {statisticKeys.map((key) => <StatRow key={key} label={metricLabels[key]} home={homeStats[key]} away={awayStats[key]} />)}
              </div>
              <div className="notice">1T + 2T: {statsPayload.reconciled ? "reconciliado" : "no confirmado"} · marcador: {statsPayload.score_reconciled ? "reconciliado" : "no confirmado"}.</div>
              {period === "total" ? <Boxscore value={statsPayload.boxscore} /> : null}
            </>
          )}
        </article>
      </div>
    </>
  );
}

function StatRow({ label, home, away }: { label: string; home: unknown; away: unknown }) {
  const left = Math.max(0, Number(home) || 0);
  const right = Math.max(0, Number(away) || 0);
  const total = left + right;
  return <div className="stats-row visual-stat" role="row"><span>{label}<i><b style={{ width: `${total ? left / total * 100 : 50}%` }} /></i></span><strong>{String(home ?? 0)}</strong><strong>{String(away ?? 0)}</strong></div>;
}

function Boxscore({ value }: { value: unknown }) {
  const rows = Array.isArray(value) ? value.map(record) : [];
  const bySide = new Map(rows.map((row) => [String(row.side), record(row.statistics)]));
  const home = bySide.get("home") ?? {};
  const away = bySide.get("away") ?? {};
  return (
    <div className="context-grid compact boxscore-grid">
      {["possessionPct", "totalPasses", "accuratePasses", "totalTackles"].map((key) => <div key={key}><span>{key.replaceAll(/([A-Z])/g, " $1")}</span><strong>{String(home[key] ?? "N/D")} · {String(away[key] ?? "N/D")}</strong></div>)}
    </div>
  );
}
