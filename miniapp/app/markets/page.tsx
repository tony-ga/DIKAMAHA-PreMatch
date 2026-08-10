"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { EntityImage } from "@/components/entity-image";
import { ProviderMarketTape } from "@/components/provider-market-tape";
import { CatalogWarning, Metric, PageHeader, StatePanel } from "@/components/ui";
import { api, type League, queryString, record } from "@/lib/client-api";

function today(): string {
  return new Date().toISOString().slice(0, 10).replaceAll("-", "");
}

function dateInputValue(value: string): string {
  return value.length === 8
    ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
    : "";
}

function MarketFixture({ value }: { value: Record<string, unknown> }) {
  const home = record(value.home_team);
  const away = record(value.away_team);
  const homeName = String(home.name || "Equipo local");
  const awayName = String(away.name || "Equipo visitante");
  return (
    <article className="data-panel market-fixture">
      <div className="market-fixture-teams">
        <div><EntityImage source={String(home.logo || "")} label={homeName} size={48} /><strong>{homeName}</strong></div>
        <span>VS</span>
        <div><EntityImage source={String(away.logo || "")} label={awayName} size={48} /><strong>{awayName}</strong></div>
      </div>
      <ProviderMarketTape value={value.market_context} homeName={homeName} awayName={awayName} />
    </article>
  );
}

export default function MarketsPage() {
  const [league, setLeague] = useState("");
  const [date, setDate] = useState(today);
  const leagues = useQuery({ queryKey: ["explorer-leagues"], queryFn: () => api<{ leagues: League[] }>("/api/explorer/leagues") });
  const effectiveLeague = league || leagues.data?.leagues[0]?.slug || "";
  const query = useQuery({
    queryKey: ["provider-markets", effectiveLeague, date],
    queryFn: () => api<Record<string, unknown>>(`/api/provider/markets${queryString({ league: effectiveLeague, date })}`),
    enabled: Boolean(effectiveLeague && date),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
  const fixtures = Array.isArray(query.data?.fixtures) ? query.data.fixtures.map(record) : [];
  const source = String(query.data?.source_name || "Proveedor");

  return (
    <>
      <PageHeader eyebrow={`${source.toUpperCase()} · ACTIVE ODDS · AUTO 30 S`} title="Pronósticos globales" action={<button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar mercados">↻</button>} />
      <div className="notice">Apertura, cierre y valor live permanecen separados de las probabilidades analíticas y de todos los modelos DIKAMAHA.</div>
      <div className="form-grid filter-grid market-filters">
        <div className="field"><label htmlFor="market-league">Liga o torneo</label><select id="market-league" value={effectiveLeague} onChange={(event) => setLeague(event.target.value)}><option value="">Selecciona una competición</option>{leagues.data?.leagues.map((item) => <option key={item.slug} value={item.slug}>{item.name}</option>)}</select></div>
        <div className="field"><label htmlFor="market-date">Fecha</label><input id="market-date" type="date" value={dateInputValue(date)} onChange={(event) => setDate(event.target.value.replaceAll("-", ""))} /></div>
      </div>
      {leagues.isError ? <CatalogWarning onRetry={() => void leagues.refetch()} /> : null}
      <div className="metric-grid compact-metrics"><Metric label="Partidos con mercado" value={(query.data?.count as number | undefined) ?? "—"} accent /><Metric label="Competición" value={effectiveLeague || "—"} /><Metric label="Refresco" value="30 s" /></div>
      {query.isError ? <StatePanel title="Mercado no disponible" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>El proveedor no respondió para esta competición y fecha.</StatePanel> : query.isLoading ? <StatePanel title="Consultando active odds">Buscando líneas de apertura, cierre y live publicadas.</StatePanel> : fixtures.length ? <div className="stack">{fixtures.map((fixture) => <MarketFixture key={String(fixture.event_id)} value={fixture} />)}</div> : <StatePanel title="Sin líneas publicadas">No hay `active odds` para la competición y fecha seleccionadas.</StatePanel>}
    </>
  );
}
