"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader, StatePanel } from "@/components/ui";
import { EntityImage } from "@/components/entity-image";
import { api, queryString, record } from "@/lib/client-api";

const preferredStats = [
  ["appearances", "Partidos"], ["totalGoals", "Goles"],
  ["goalAssists", "Asistencias"], ["totalShots", "Tiros"],
  ["shotsOnTarget", "A puerta"], ["yellowCards", "Amarillas"],
  ["redCards", "Rojas"], ["saves", "Atajadas"],
] as const;

export function PlayerDetail({ league, teamId, playerId }: { league: string; teamId: string; playerId: string }) {
  const query = useQuery({
    queryKey: ["player", league, teamId, playerId],
    queryFn: () => api<Record<string, unknown>>(`/api/explorer/player${queryString({ league, team_id: teamId, player_id: playerId })}`),
    enabled: Boolean(league && teamId && playerId),
  });
  if (query.isError) return <StatePanel title="Perfil no disponible">No se completan datos personales ausentes.</StatePanel>;
  if (query.isLoading) return <StatePanel title="Cargando perfil">Consultando perfil y estadísticas publicadas.</StatePanel>;
  const payload = query.data ?? {};
  const team = record(payload.team);
  const statistics = Array.isArray(payload.statistics) ? payload.statistics.map(record) : [];
  const values = new Map(statistics.map((row) => [String(row.name), String(row.value ?? "0")]));
  return (
    <>
      <PageHeader eyebrow={`${league} · ${String(team.name ?? "JUGADOR")}`} title={String(payload.name ?? "Jugador")} />
      <div className="stack">
        <div className="player-hero"><EntityImage source={String(payload.headshot || "")} label={String(payload.name ?? "Jugador")} kind="player" size={96} /><div><strong>{String(payload.name ?? "Jugador")}</strong><span>{String(payload.position ?? "Posición no publicada")}</span></div></div>
        <article className="data-panel">
          <p className="eyebrow">PERFIL PUBLICADO</p>
          <div className="context-grid">
            <div><span>Posición</span><strong>{String(payload.position ?? "N/D")}</strong></div>
            <div><span>Edad</span><strong>{String(payload.age ?? "N/D")}</strong></div>
            <div><span>Altura</span><strong>{String(payload.height ?? "N/D")}</strong></div>
            <div><span>Peso</span><strong>{String(payload.weight ?? "N/D")}</strong></div>
            <div><span>Nacionalidad</span><strong>{String(payload.citizenship ?? "N/D")}</strong></div>
            <div><span>Estado</span><strong>{payload.active ? "Activo" : "No confirmado"}</strong></div>
          </div>
        </article>
        <article className="data-panel">
          <p className="eyebrow">ESTADÍSTICAS DE TEMPORADA</p>
          {statistics.length ? <div className="stats-table">{preferredStats.map(([key, label]) => <div className="stats-row compact" key={key}><span>{label}</span><strong>{values.get(key) ?? "0"}</strong></div>)}</div> : <p className="muted">Estadísticas acumuladas no publicadas por el proveedor.</p>}
        </article>
        <div className="notice">Perfil informativo. No se utiliza para props ni altera la predicción.</div>
      </div>
    </>
  );
}
