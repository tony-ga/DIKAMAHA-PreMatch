"use client";

import { useQuery } from "@tanstack/react-query";

import { CatalogWarning, FixtureCard, Metric, PageHeader, StatePanel, TruncatedCatalogNotice } from "@/components/ui";
import { api, type Catalog, type League, queryString } from "@/lib/client-api";
import { useState } from "react";

type ScanProgress = { status: string; scanned: number; total: number };

// Barrido en frío medido: 63 ligas x 3 días (D-1/D/D+1) con 12 conexiones
// concurrentes tardan ~30s -no es el proceso, es cuánta concurrencia tolera
// el proveedor de datos antes de frenar cada respuesta-. Antes esta pantalla
// sólo mostraba un texto estático durante toda esa espera. Ahora sondea el
// avance real del barrido en el servidor (`GET /v1/live/progress`) y dibuja
// una barra cuyo ancho es la fracción real ya escaneada, no una animación
// indeterminada inventada.
function LiveScanStatus({ snapshot }: { snapshot: ScanProgress | undefined }) {
  const total = snapshot?.total ?? 0;
  const scanned = Math.max(0, Math.min(snapshot?.scanned ?? 0, total));
  const known = total > 0;
  const percent = known ? Math.round((scanned / total) * 100) : 0;
  return (
    <section className="state-panel" role="status" aria-live="polite">
      <div className="progress-track">
        <div
          className={known ? "progress-fill-real" : "progress-fill"}
          style={known ? { width: `${percent}%` } : undefined}
        />
      </div>
      <h2>Escaneando ligas</h2>
      <div className="muted">
        {known
          ? `${scanned} de ${total} combinaciones liga/fecha revisadas (${percent}%).`
          : "Consultando fixtures activos en DIKAMAHA."}
      </div>
    </section>
  );
}

export default function LivePage() {
  const [league, setLeague] = useState("");
  const leagues = useQuery({ queryKey: ["explorer-leagues"], queryFn: () => api<{ leagues: League[] }>("/api/explorer/leagues") });
  const query = useQuery({
    queryKey: ["live", league],
    queryFn: () => api<Catalog>(`/api/live${queryString({ limit: 20, leagues: league })}`),
    refetchInterval: 20_000,
    refetchOnWindowFocus: true,
  });
  const progress = useQuery({
    queryKey: ["live-progress", league],
    queryFn: () => api<ScanProgress>(`/api/live/progress${queryString({ limit: 20, leagues: league })}`),
    enabled: query.isLoading,
    refetchInterval: query.isLoading ? 400 : false,
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
      <TruncatedCatalogNotice leagues={query.data?.leagues_with_hidden_fixtures ?? []} />
      {query.isError ? (
        <StatePanel title="No pudimos actualizar" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>La API sigue protegida; no se reutilizan datos stale como si fueran live.</StatePanel>
      ) : query.isLoading ? (
        <LiveScanStatus snapshot={progress.data} />
      ) : query.data?.fixtures.length ? (
        <div className="fixture-list two-column">{query.data.fixtures.map((fixture) => <FixtureCard key={fixture.match_id} fixture={fixture} live />)}</div>
      ) : (
        <StatePanel title="No hay partidos activos">La vista volverá a consultar automáticamente. Tus otras secciones siguen disponibles abajo.</StatePanel>
      )}
    </>
  );
}
