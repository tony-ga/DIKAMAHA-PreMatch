"use client";

import { useQuery } from "@tanstack/react-query";

import { CatalogWarning, FixtureCard, Metric, PageHeader, StatePanel } from "@/components/ui";
import { api, type Catalog, type League, queryString } from "@/lib/client-api";
import { useState } from "react";

export default function LivePage() {
  const [league, setLeague] = useState("");
  const leagues = useQuery({ queryKey: ["explorer-leagues"], queryFn: () => api<{ leagues: League[] }>("/api/explorer/leagues") });
  const query = useQuery({
    queryKey: ["live", league],
    queryFn: () => api<Catalog>(`/api/live${queryString({ limit: 20, leagues: league })}`),
    refetchInterval: 20_000,
    refetchOnWindowFocus: true,
  });
  return (
    <>
      <PageHeader eyebrow="IN-PLAY · AUTO 20 S" title="Partidos en vivo" action={
        <button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar partidos">↻</button>
      } />
      <div className="notice live-catalog-notice"><span className="live-pill"><i /> AUTO</span><span>El catálogo se sincroniza cada 20 segundos. Markov es el baseline live; Hawkes sólo ajusta memoria corta y permanece shadow.</span></div>
      <div style={{ height: 14 }} />
      {leagues.isError ? <CatalogWarning onRetry={() => void leagues.refetch()} /> : null}
      <div className="form-grid filter-grid">
        <div className="field"><label htmlFor="live-league">Liga</label><select id="live-league" value={league} onChange={(event) => setLeague(event.target.value)}><option value="">Todas las ligas</option>{leagues.data?.leagues.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}</select></div>
      </div>
      <div className="metric-grid compact-metrics"><Metric label="Activos" value={query.data?.count ?? "—"} accent /><Metric label="Ligas escaneadas" value={query.data?.league_count ?? (league ? 1 : "—")} /><Metric label="Ventana de fechas" value={`${query.data?.date_count ?? 3} días`} /></div>
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
