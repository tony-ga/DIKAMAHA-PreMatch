"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { FixtureCard, PageHeader, StatePanel } from "@/components/ui";
import { api, type Catalog, type ExplorerDate, type League, queryString } from "@/lib/client-api";

export default function UpcomingPage() {
  const [league, setLeague] = useState("");
  const [date, setDate] = useState("");
  const leagues = useQuery({ queryKey: ["explorer-leagues"], queryFn: () => api<{ leagues: League[] }>("/api/explorer/leagues") });
  const dates = useQuery({ queryKey: ["explorer-dates", "future"], queryFn: () => api<{ dates: ExplorerDate[] }>("/api/explorer/dates?mode=future&days=14") });
  const query = useQuery({
    queryKey: ["upcoming", league, date],
    queryFn: () => api<Catalog>(`/api/upcoming${queryString({ limit: 20, leagues: league, date })}`),
    refetchInterval: 60_000,
  });
  return (
    <>
      <PageHeader eyebrow="PRE-MATCH · CORTE CAUSAL" title="Próximos partidos" />
      <div className="form-grid filter-grid" style={{ marginBottom: 14 }}>
        <div className="field">
          <label htmlFor="league-filter">Liga</label>
          <select id="league-filter" value={league} onChange={(event) => setLeague(event.target.value)}>
            <option value="">Todas las ligas</option>
            {leagues.data?.leagues.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="date-filter">Fecha</label>
          <select id="date-filter" value={date} onChange={(event) => setDate(event.target.value)}>
            <option value="">Todas las fechas</option>
            {dates.data?.dates.map((item) => <option key={item.date} value={item.date}>{item.label}</option>)}
          </select>
        </div>
      </div>
      {query.isError ? (
        <StatePanel title="Calendario no disponible" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>No se generan fixtures ni predicciones inventadas.</StatePanel>
      ) : query.data?.fixtures.length ? (
        <div className="fixture-list two-column">{query.data.fixtures.map((fixture) => <FixtureCard key={fixture.match_id} fixture={fixture} />)}</div>
      ) : (
        <StatePanel title={query.isLoading ? "Cargando calendario" : "Sin coincidencias"}>Prueba otra liga o vuelve más tarde.</StatePanel>
      )}
    </>
  );
}
