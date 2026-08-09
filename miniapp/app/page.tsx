"use client";

import { useQueries } from "@tanstack/react-query";
import Link from "next/link";

import { FixtureCard, Metric, SectionTitle, StatePanel } from "@/components/ui";
import { api, type Catalog } from "@/lib/client-api";

type ModelsPayload = { status: string; models: unknown[] };

export default function DashboardPage() {
  const [live, upcoming, models] = useQueries({ queries: [
    {
      queryKey: ["live", "dashboard"],
      queryFn: () => api<Catalog>("/api/live?limit=4"),
      refetchInterval: 25_000,
    },
    {
      queryKey: ["upcoming", "dashboard"],
      queryFn: () => api<Catalog>("/api/upcoming?limit=4"),
      refetchInterval: 60_000,
    },
    {
      queryKey: ["models"],
      queryFn: () => api<ModelsPayload>("/api/models"),
    },
  ] });

  return (
    <>
      <section className="hero">
        <p className="eyebrow">FOOTBALL PROBABILITY SYSTEM</p>
        <h1>Lee el partido. Antes y durante.</h1>
        <p className="hero-copy">
          La capacidad estructural pre-match y el régimen Markov Live, con Hawkes como memoria corta residual donde existe soporte.
        </p>
        <div className="hero-actions">
          <Link className="primary-button" href="/live">Ver partidos en vivo</Link>
          <Link className="secondary-button" href="/upcoming">Explorar próximos</Link>
        </div>
      </section>

      <div className="metric-grid">
        <Metric label="En vivo ahora" value={live.data?.count ?? "—"} accent />
        <Metric label="Próximos" value={upcoming.data?.count ?? "—"} />
        <Metric label="Modelos activos" value={models.data?.models.length ?? "—"} />
      </div>

      <SectionTitle aside={<Link className="section-link" href="/live">Ver todos →</Link>}>Radar live</SectionTitle>
      {live.isError ? (
        <StatePanel title="Live no disponible">La API conserva sus fallos aislados. Intenta actualizar en unos segundos.</StatePanel>
      ) : live.isLoading ? (
        <StatePanel title="Buscando partidos">Consultando el catálogo central de DIKAMAHA.</StatePanel>
      ) : live.data?.fixtures.length ? (
        <div className="fixture-list two-column">{live.data.fixtures.map((fixture) => <FixtureCard key={fixture.match_id} fixture={fixture} live />)}</div>
      ) : (
        <StatePanel title="Sin partidos activos">El panel se actualizará automáticamente cuando el proveedor publique un fixture live.</StatePanel>
      )}

      <SectionTitle aside={<Link className="section-link" href="/upcoming">Calendario →</Link>}>A continuación</SectionTitle>
      {upcoming.data?.fixtures.length ? (
        <div className="fixture-list two-column">{upcoming.data.fixtures.map((fixture) => <FixtureCard key={fixture.match_id} fixture={fixture} />)}</div>
      ) : (
        <StatePanel title="Calendario en espera">No hay próximos partidos disponibles para los filtros activos.</StatePanel>
      )}
    </>
  );
}
