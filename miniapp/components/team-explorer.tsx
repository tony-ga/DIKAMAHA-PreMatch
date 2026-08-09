"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useDeferredValue, useState } from "react";

import { PageHeader, StatePanel } from "@/components/ui";
import { api, type League, type Team, queryString } from "@/lib/client-api";

export function TeamExplorer() {
  const [league, setLeague] = useState("");
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const leagues = useQuery({
    queryKey: ["explorer-leagues"],
    queryFn: () => api<{ leagues: League[] }>("/api/explorer/leagues"),
  });
  const teams = useQuery({
    queryKey: ["explorer-teams", league, deferredSearch],
    queryFn: () => api<{ teams: Team[] }>(`/api/explorer/teams${queryString({ league, query: deferredSearch })}`),
    enabled: Boolean(league),
  });
  return (
    <>
      <PageHeader eyebrow="EQUIPOS Y JUGADORES" title="Plantillas" />
      <div className="form-grid filter-grid">
        <div className="field">
          <label htmlFor="team-league">Liga</label>
          <select id="team-league" value={league} onChange={(event) => setLeague(event.target.value)}>
            <option value="">Selecciona una liga</option>
            {leagues.data?.leagues.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}
          </select>
        </div>
        <div className="field">
          <label htmlFor="team-search">Buscar equipo</label>
          <input id="team-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Ej. Cruz Azul" disabled={!league} />
        </div>
      </div>
      {!league ? <StatePanel title="Selecciona una liga">Podrás listar equipos, buscar por nombre y abrir plantillas y jugadores.</StatePanel> : teams.isError ? (
        <StatePanel title="Equipos no disponibles" action={<button className="primary-button" onClick={() => void teams.refetch()}>Reintentar</button>}>No se muestran coincidencias inventadas.</StatePanel>
      ) : teams.isLoading ? <StatePanel title="Cargando equipos">Consultando DIKAMAHA.</StatePanel> : teams.data?.teams.length ? (
        <div className="team-grid">
          {teams.data.teams.map((team) => <Link className="team-card" key={team.id} href={`/explore/teams/${team.id}${queryString({ league })}`}><div className="team-mark">{team.abbreviation || team.name.slice(0, 3).toUpperCase()}</div><div><strong>{team.name}</strong><span>{team.abbreviation || league}</span></div><b>→</b></Link>)}
        </div>
      ) : <StatePanel title="Sin coincidencias">Prueba un nombre más corto.</StatePanel>}
    </>
  );
}
