"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { FixtureCard, PageHeader, StatePanel } from "@/components/ui";
import { api, type Catalog } from "@/lib/client-api";

export default function UpcomingPage() {
  const [league, setLeague] = useState("");
  const query = useQuery({
    queryKey: ["upcoming", league],
    queryFn: () => api<Catalog>(`/api/upcoming?limit=20${league ? `&leagues=${encodeURIComponent(league)}` : ""}`),
    refetchInterval: 60_000,
  });
  return (
    <>
      <PageHeader eyebrow="PRE-MATCH · CORTE CAUSAL" title="Próximos partidos" />
      <div className="field" style={{ marginBottom: 14 }}>
        <label htmlFor="league-filter">Filtrar por liga</label>
        <input id="league-filter" value={league} onChange={(event) => setLeague(event.target.value)} placeholder="Ej. mex.1, eng.1" />
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
