"use client";

import { useQuery } from "@tanstack/react-query";

import { FixtureCard, PageHeader, StatePanel } from "@/components/ui";
import { api, type Catalog } from "@/lib/client-api";

export default function LivePage() {
  const query = useQuery({
    queryKey: ["live"],
    queryFn: () => api<Catalog>("/api/live?limit=20"),
    refetchInterval: 25_000,
  });
  return (
    <>
      <PageHeader eyebrow="IN-PLAY · REFRESCO 25 S" title="Partidos en vivo" action={
        <button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar partidos">↻</button>
      } />
      <div className="notice">Markov es el baseline live. Hawkes sólo ajusta memoria corta en las ligas admitidas y permanece shadow.</div>
      <div style={{ height: 14 }} />
      {query.isError ? (
        <StatePanel title="No pudimos actualizar" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>La API sigue protegida; no se reutilizan datos stale como si fueran live.</StatePanel>
      ) : query.isLoading ? (
        <StatePanel title="Escaneando ligas">Consultando fixtures activos en DIKAMAHA.</StatePanel>
      ) : query.data?.fixtures.length ? (
        <div className="fixture-list two-column">{query.data.fixtures.map((fixture) => <FixtureCard key={fixture.match_id} fixture={fixture} live />)}</div>
      ) : (
        <StatePanel title="No hay partidos activos">La vista volverá a consultar automáticamente. Tus otras secciones siguen disponibles abajo.</StatePanel>
      )}
    </>
  );
}
