"use client";

import { useQuery } from "@tanstack/react-query";

import { EntityImage } from "@/components/entity-image";
import { Metric, PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { api, percentage, queryString, record } from "@/lib/client-api";

function today(): string {
  return new Date().toISOString().slice(0, 10).replaceAll("-", "");
}

const METRICS: Record<string, string> = {
  corners: "Córners",
  shots: "Tiros",
  shots_on_target: "Tiros a puerta",
  yellow_cards: "Tarjetas",
  goals: "Goles",
  result: "Resultado",
};

const PERIODS: Record<string, string> = {
  first_half: "1T",
  second_half: "2T",
  full_match: "",
};

// El lado se nombra con el equipo al que pertenece: "Córners de Betis" dice
// bastante más que "Córners local", sobre todo en un menú que mezcla partidos.
function subjectLabel(
  pick: Record<string, unknown>, homeName: string, awayName: string,
): string {
  const metric = METRICS[String(pick.metric)] ?? String(pick.market);
  const side = String(pick.team_side);
  if (side === "home") return `${metric} de ${homeName}`;
  if (side === "away") return `${metric} de ${awayName}`;
  if (side === "total") return `${metric} de ambos equipos`;
  return metric;
}

function pickLabel(
  pick: Record<string, unknown>, homeName: string, awayName: string,
): string {
  const subject = subjectLabel(pick, homeName, awayName);
  const period = PERIODS[String(pick.period)] ?? "";
  const line = pick.line;
  if (typeof line !== "number") {
    return [subject, period].filter(Boolean).join(" · ");
  }
  const sense = String(pick.direction) === "under" ? "Menos de" : "Más de";
  return [subject, period, `${sense} ${line}`].filter(Boolean).join(" · ");
}

function PickCard({ value }: { value: Record<string, unknown> }) {
  const fixture = record(value.fixture);
  const interval = Array.isArray(value.observed_ci95) ? value.observed_ci95 : [];
  const homeName = String(fixture.home_team_name || `Equipo ${fixture.home_team_id}`);
  const awayName = String(fixture.away_team_name || `Equipo ${fixture.away_team_id}`);
  const kickoff = typeof fixture.kickoff_ts === "string"
    ? new Date(fixture.kickoff_ts).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" })
    : "—";
  const modelEdge = String(value.edge_source) === "model_edge";
  return (
    <article className="data-panel">
      <div className="ladder-head">
        <span>{String(fixture.league_slug)} · {kickoff}</span>
        <ShadowBadge />
      </div>
      <div className="market-fixture-teams">
        <div><EntityImage source={String(fixture.home_team_logo || "")} label={homeName} size={34} /><strong>{homeName}</strong></div>
        <span>VS</span>
        <div><EntityImage source={String(fixture.away_team_logo || "")} label={awayName} size={34} /><strong>{awayName}</strong></div>
      </div>
      <div className="ladder-group high-prob-pick">
        <div className="ladder-head">
          <span>{pickLabel(value, homeName, awayName)}</span>
          <strong>{percentage(value.observed_rate)}</strong>
        </div>
        <small className="ladder-edge">
          Acierto histórico de este mercado en este nivel de confianza, sobre{" "}
          {String(value.sample_size)} picks. Entre {percentage(interval[0])} y {percentage(interval[1])}.
        </small>
        <div className="subscription-row">
          <span>{modelEdge ? "Ventaja del modelo" : "Ventaja de la tasa base"}</span>
          <strong>{modelEdge ? "✓ mide" : "≈ mercado"}</strong>
        </div>
        <small className="ladder-edge">
          {modelEdge
            ? "El modelo supera de forma estadísticamente significativa a la estrategia base en este tramo."
            : "El pick acierta mucho porque el mercado ya acierta mucho por sí solo; el modelo no añade ventaja demostrada."}
        </small>
        <div className="subscription-row">
          <span>El modelo declara</span><strong>{percentage(value.model_probability)}</strong>
        </div>
      </div>
    </article>
  );
}

export default function HighProbabilityPage() {
  const query = useQuery({
    queryKey: ["high-probability"],
    queryFn: () => api<Record<string, unknown>>(`/api/high-probability${queryString({ date: today(), limit: 12 })}`),
    refetchInterval: 120_000,
    refetchOnWindowFocus: true,
  });
  const picks = Array.isArray(query.data?.picks) ? query.data.picks.map(record) : [];
  const provenance = record(query.data?.provenance);
  const unavailable = String(query.data?.status) === "unavailable";

  return (
    <>
      <PageHeader
        eyebrow="FASE 122 · FIABILIDAD VERIFICADA · AUTO 2 MIN"
        title="Mayor probabilidad"
        action={<button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar picks">↻</button>}
      />
      <div className="notice">
        El porcentaje que ves es el <strong>acierto histórico real</strong> de ese mercado en ese
        nivel de confianza, no la probabilidad que declara el modelo. Sólo aparecen mercados cuya
        fiabilidad superó el gate de Fase 122; 1X2, Más de 2.5 y Ambos marcan no lo superaron en
        ningún tramo y por eso nunca aparecen aquí.
      </div>
      <div className="metric-grid compact-metrics">
        <Metric label="Picks del día" value={(query.data?.count as number | undefined) ?? "—"} accent />
        <Metric label="Partidos revisados" value={(query.data?.fixtures_scanned as number | undefined) ?? "—"} />
        <Metric label="Mercados aptos" value={(provenance.eligible_cells as number | undefined) ?? "—"} />
      </div>
      {query.isError ? (
        <StatePanel title="Servicio no disponible" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>
          No se pudo consultar el menú de mayor probabilidad.
        </StatePanel>
      ) : unavailable ? (
        <StatePanel title="Gate no disponible">
          El artefacto de fiabilidad de Fase 122 no está cargado, de modo que no se expone ningún pick.
        </StatePanel>
      ) : query.isLoading ? (
        <StatePanel title="Evaluando partidos de hoy">
          Calculando los mercados de cada partido y filtrando por fiabilidad demostrada.
        </StatePanel>
      ) : picks.length ? (
        <div className="stack">{picks.map((pick, index) => <PickCard key={`${String(record(pick.fixture).match_id)}-${String(pick.market)}-${index}`} value={pick} />)}</div>
      ) : (
        <StatePanel title="Hoy no hay ningún pick que supere el gate">
          Ningún mercado de los partidos de hoy alcanza un nivel de confianza cuya fiabilidad esté
          demostrada. Es un resultado normal y frecuente: preferimos no mostrar nada antes que
          mostrar un pick que no se sostiene.
        </StatePanel>
      )}
    </>
  );
}
