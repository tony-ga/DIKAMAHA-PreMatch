"use client";

import { useQuery } from "@tanstack/react-query";

import { StatePanel } from "@/components/ui";
import { api, queryString, record } from "@/lib/client-api";

function names(value: unknown): string {
  if (!Array.isArray(value)) return "No publicado";
  const result = value.map((item) => String(record(item).name ?? "")).filter(Boolean);
  return result.length ? result.slice(0, 3).join(", ") : "No publicado";
}

function availability(value: unknown): string {
  const row = record(value);
  if (!Object.keys(row).length || row.injury_report_status === "not_published") {
    return "Reporte no publicado";
  }
  const injuries = Array.isArray(row.published_injuries) ? row.published_injuries.length : 0;
  return `${injuries} incidencias publicadas · roster ${String(row.roster_count ?? "N/D")}`;
}

export function FixtureContext({ league, eventId, compact = false }: {
  league: string;
  eventId: string;
  compact?: boolean;
}) {
  const query = useQuery({
    queryKey: ["fixture-context", league, eventId],
    queryFn: () => api<Record<string, unknown>>(`/api/explorer/fixture/context${queryString({ league, event_id: eventId })}`),
    enabled: Boolean(league && eventId),
  });
  if (query.isLoading) return <StatePanel title="Cargando contexto">Consultando el snapshot raw-first disponible.</StatePanel>;
  if (query.isError || query.data?.status !== "available") {
    return <StatePanel title="Contexto aún no publicado">La ausencia se conserva explícitamente y no altera la predicción.</StatePanel>;
  }
  const payload = query.data;
  const fixture = record(payload.fixture);
  const competition = record(payload.competition);
  const venue = record(payload.venue);
  const teams = record(payload.teams);
  const home = record(teams.home);
  const away = record(teams.away);
  const teamContext = record(payload.team_context);
  const homeStanding = record(record(teamContext.home).standing);
  const awayStanding = record(record(teamContext.away).standing);
  const availabilityRows = record(payload.availability);
  return (
    <article className="data-panel context-panel">
      <p className="eyebrow">CONTEXTO INFORMATIVO · NO MODELO</p>
      <h3>{String(home.name ?? "Local")} vs {String(away.name ?? "Visitante")}</h3>
      <div className={compact ? "context-grid compact" : "context-grid"}>
        <div><span>Competición</span><strong>{String(competition.name ?? "No publicada")}</strong></div>
        <div><span>Fase</span><strong>{String(competition.phase ?? "No publicada")}</strong></div>
        <div><span>Sede</span><strong>{[venue.name, venue.city, venue.country].filter(Boolean).join(" · ") || "No publicada"}</strong></div>
        <div><span>Kickoff</span><strong>{fixture.kickoff_ts ? new Date(String(fixture.kickoff_ts)).toLocaleString("es-MX") : "N/D"}</strong></div>
        <div><span>Árbitros</span><strong>{names(payload.officials)}</strong></div>
        <div><span>Transmisión</span><strong>{names(payload.broadcasts)}</strong></div>
      </div>
      {!compact ? (
        <div className="stack context-teams">
          <div className="subscription-row"><span>{String(home.name ?? "Local")} · tabla</span><strong>{homeStanding.rank ? `#${String(homeStanding.rank)} · ${String(homeStanding.points ?? "–")} pts` : "No publicada"}</strong></div>
          <div className="subscription-row"><span>{String(away.name ?? "Visitante")} · tabla</span><strong>{awayStanding.rank ? `#${String(awayStanding.rank)} · ${String(awayStanding.points ?? "–")} pts` : "No publicada"}</strong></div>
          <div className="subscription-row"><span>{String(home.name ?? "Local")} · disponibilidad</span><strong>{availability(availabilityRows.home)}</strong></div>
          <div className="subscription-row"><span>{String(away.name ?? "Visitante")} · disponibilidad</span><strong>{availability(availabilityRows.away)}</strong></div>
        </div>
      ) : null}
    </article>
  );
}
