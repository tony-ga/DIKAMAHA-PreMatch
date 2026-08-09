"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { PageHeader, StatePanel } from "@/components/ui";
import { api, type ExplorerDate, type HistoricalFixture, type League, queryString } from "@/lib/client-api";

export function MatchExplorer() {
  const [league, setLeague] = useState("");
  const [date, setDate] = useState("");
  const leagues = useQuery({
    queryKey: ["explorer-leagues"],
    queryFn: () => api<{ leagues: League[] }>("/api/explorer/leagues"),
  });
  const dates = useQuery({
    queryKey: ["explorer-dates", "past"],
    queryFn: () => api<{ dates: ExplorerDate[] }>("/api/explorer/dates?mode=past&days=14"),
  });
  const fixtures = useQuery({
    queryKey: ["explorer-fixtures", league, date],
    queryFn: () => api<{ fixtures: HistoricalFixture[] }>(`/api/explorer/fixtures${queryString({ league, date })}`),
    enabled: Boolean(league && date),
  });
  return (
    <>
      <PageHeader eyebrow="ARCHIVO RAW-FIRST" title="Centro de partidos" />
      <div className="form-grid filter-grid">
        <div className="field">
          <label htmlFor="history-league">Liga</label>
          <select id="history-league" value={league} onChange={(event) => setLeague(event.target.value)}>
            <option value="">Selecciona una liga</option>
            {leagues.data?.leagues.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="history-date">Fecha</label>
          <select id="history-date" value={date} onChange={(event) => setDate(event.target.value)}>
            <option value="">Selecciona una fecha</option>
            {dates.data?.dates.map((item) => <option key={item.date} value={item.date}>{item.label}</option>)}
          </select>
        </div>
      </div>
      {!league || !date ? (
        <StatePanel title="Selecciona liga y fecha">Después podrás abrir contexto, eventos clave, play-by-play completo y estadísticas.</StatePanel>
      ) : fixtures.isError ? (
        <StatePanel title="Partidos no disponibles" action={<button className="primary-button" onClick={() => void fixtures.refetch()}>Reintentar</button>}>La API no inventa partidos cuando el proveedor no publica el catálogo.</StatePanel>
      ) : fixtures.isLoading ? (
        <StatePanel title="Cargando partidos">Consultando el scoreboard mediante DIKAMAHA.</StatePanel>
      ) : fixtures.data?.fixtures.length ? (
        <div className="fixture-list two-column">
          {fixtures.data.fixtures.map((fixture) => (
            <Link
              className="fixture-card"
              key={fixture.match_id}
              href={`/explore/matches/${fixture.match_id}${queryString({
                league,
                competition: fixture.competition_id,
                home: fixture.home_team_name,
                away: fixture.away_team_name,
              })}`}
            >
              <div className="fixture-meta"><span>{fixture.status_detail ?? league}</span><span>Ver datos →</span></div>
              <div className="score-row">
                <div><strong>{fixture.home_team_name ?? "Local"}</strong><strong>{fixture.away_team_name ?? "Visitante"}</strong></div>
                <div className="score"><b>{fixture.home_score ?? "–"}</b><b>{fixture.away_score ?? "–"}</b></div>
              </div>
            </Link>
          ))}
        </div>
      ) : <StatePanel title="Sin partidos publicados">Prueba otra fecha o liga.</StatePanel>}
    </>
  );
}
