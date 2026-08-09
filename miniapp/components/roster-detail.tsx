"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { FavoriteButton } from "@/components/favorite-button";
import { EntityImage } from "@/components/entity-image";
import { PageHeader, StatePanel } from "@/components/ui";
import { api, type Player, queryString, record } from "@/lib/client-api";

export function RosterDetail({ league, teamId }: { league: string; teamId: string }) {
  const query = useQuery({
    queryKey: ["team-roster", league, teamId],
    queryFn: () => api<Record<string, unknown>>(`/api/explorer/team/roster${queryString({ league, team_id: teamId })}`),
    enabled: Boolean(league && teamId),
  });
  if (query.isError) return <StatePanel title="Plantilla no disponible">El proveedor no publicó un roster utilizable para este equipo.</StatePanel>;
  if (query.isLoading) return <StatePanel title="Cargando plantilla">Consultando el roster mediante DIKAMAHA.</StatePanel>;
  const payload = query.data ?? {};
  const team = record(payload.team);
  const players = Array.isArray(payload.players) ? payload.players as Player[] : [];
  const name = String(team.name ?? `Equipo ${teamId}`);
  return (
    <>
      <PageHeader eyebrow={`${league} · ROSTER`} title={name} action={<FavoriteButton entityType="team" entityId={teamId} label={name} metadata={{ league }} />} />
      <div className="roster-summary"><EntityImage source={String(team.logo || "")} label={name} size={64} /><div><strong>{players.length}</strong><span>jugadores publicados</span></div></div>
      {players.length ? <div className="roster-list">{players.map((player) => <Link className="player-row" key={player.id} href={`/explore/players/${player.id}${queryString({ league, team: teamId })}`}><EntityImage source={player.headshot} label={player.name} kind="player" /><span className="jersey">{player.jersey || "–"}</span><div><strong>{player.name}</strong><small>{player.position || "Posición no publicada"}{player.age ? ` · ${player.age} años` : ""}</small></div><b>→</b></Link>)}</div> : <StatePanel title="Roster vacío">La ausencia se muestra explícitamente.</StatePanel>}
    </>
  );
}
