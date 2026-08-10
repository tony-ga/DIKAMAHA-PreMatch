"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";

import { api, percentage, queryString, record } from "@/lib/client-api";

const ProbabilityChart = dynamic(() => import("@/components/probability-chart"), { ssr: false });
const ProbabilityHistoryChart = dynamic(() => import("@/components/provider-probability-history-chart"), { ssr: false });

type Props = {
  eventId: string;
  league: string;
  scope: "pre_match" | "live";
  homeName: string;
  awayName: string;
};

type HistoryPoint = { minute: number; home: number; draw: number; away: number };

function normalizedHistory(value: unknown): HistoryPoint[] {
  if (!Array.isArray(value)) return [];
  return value.map(record).flatMap((row) => {
    const point = {
      minute: Number(row.minute),
      home: Number(row.home),
      draw: Number(row.draw),
      away: Number(row.away),
    };
    return Object.values(point).every(Number.isFinite) ? [point] : [];
  });
}

export function ProviderPredictor({ eventId, league, scope, homeName, awayName }: Props) {
  const query = useQuery({
    queryKey: ["provider-predictor", eventId, league, scope],
    queryFn: () => api<Record<string, unknown>>(`/api/provider/predictor${queryString({ league, event_id: eventId, scope })}`),
    enabled: Boolean(eventId && league),
    staleTime: scope === "live" ? 15_000 : 60_000,
    refetchInterval: scope === "live" ? 30_000 : false,
    refetchOnWindowFocus: scope === "live",
  });

  if (query.isLoading) {
    return <article className="model-card provider-benchmark"><p className="eyebrow">BENCHMARK EXTERNO</p><h3>Consultando predictor del proveedor…</h3></article>;
  }
  if (query.isError) {
    return <article className="model-card provider-benchmark"><p className="eyebrow">BENCHMARK EXTERNO</p><h3>Fuente temporalmente no disponible</h3><p>Las predicciones DIKAMAHA siguen operativas y no dependen de esta capa.</p><button className="secondary-button" onClick={() => void query.refetch()}>Reintentar benchmark</button></article>;
  }

  const payload = query.data ?? {};
  const probabilities = record(payload.probabilities);
  const market = record(payload.market_context);
  const history = normalizedHistory(payload.history);
  const available = payload.status === "available" && ["home", "draw", "away"].every((key) => Number.isFinite(Number(probabilities[key])));

  if (!available) {
    return (
      <article className="model-card provider-benchmark unavailable">
        <div className="model-card-header"><div><p className="eyebrow">BENCHMARK EXTERNO</p><h3>Predictor no publicado</h3></div><span className="mode-badge benchmark">ADICIONAL</span></div>
        <p>El proveedor externo no publica probabilidades analíticas 1X2 para este partido. No se sustituyen ni estiman con datos de mercado.</p>
        {market.status === "financial_isolated_available" ? <p className="financial-isolation">Contexto de mercado detectado, aislado y no consumido por los modelos.</p> : null}
      </article>
    );
  }

  const values = [
    { name: homeName, value: Number(probabilities.home), color: "var(--mint)" },
    { name: "Empate", value: Number(probabilities.draw), color: "var(--muted)" },
    { name: awayName, value: Number(probabilities.away), color: "var(--signal)" },
  ];
  return (
    <article className="model-card provider-benchmark">
      <div className="model-card-header"><div><p className="eyebrow">BENCHMARK EXTERNO</p><h3>Predictor del proveedor</h3></div><span className="mode-badge benchmark">ADICIONAL</span></div>
      <p>Referencia independiente de comparación. No compite, recalibra ni reemplaza los modelos DIKAMAHA.</p>
      <div className="probability-grid" style={{ marginTop: 16 }}>
        {values.map((row) => <div className="probability" key={row.name}><span>{row.name}</span><strong>{percentage(row.value)}</strong></div>)}
      </div>
      <ProbabilityChart values={values} />
      {history.length > 1 ? (
        <section className="provider-history">
          <div className="panel-heading"><div><p className="eyebrow">EVOLUCIÓN</p><h3>Expectativa de resultado</h3></div><span className="provider-chip">{history.length} cortes</span></div>
          <ProbabilityHistoryChart points={history} homeName={homeName} awayName={awayName} />
        </section>
      ) : null}
      {market.status === "financial_isolated_available" ? <p className="financial-isolation">El contexto de mercado permanece aislado; no se muestran cuotas ni se usa como feature.</p> : null}
    </article>
  );
}
