"use client";

import { useQuery } from "@tanstack/react-query";

import { EntityImage } from "@/components/entity-image";
import { LoadingProgress } from "@/components/loading-progress";
import { Metric, PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { api, percentage, probabilityWidth, queryString, record } from "@/lib/client-api";

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

const PERIOD_LABELS: Record<string, string> = {
  first_half: "Primer tiempo",
  second_half: "Segundo tiempo",
  full_match: "Partido completo",
};

const PERIOD_ORDER = ["first_half", "second_half", "full_match"];

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

// El periodo ya se agrupa en un encabezado propio (ver `FixtureCard`), así
// que la etiqueta de la fila sólo lleva el sujeto y la línea.
function pickLabel(pick: Record<string, unknown>, homeName: string, awayName: string): string {
  const subject = subjectLabel(pick, homeName, awayName);
  const line = pick.line;
  if (typeof line !== "number") return subject;
  const sense = String(pick.direction) === "under" ? "Menos de" : "Más de";
  return `${subject} · ${sense} ${line}`;
}

type Pick = Record<string, unknown>;
type FixtureGroup = { fixture: Record<string, unknown>; picks: Pick[] };

function groupByFixture(picks: Pick[]): FixtureGroup[] {
  const order: string[] = [];
  const groups = new Map<string, FixtureGroup>();
  for (const pick of picks) {
    const fixture = record(pick.fixture);
    const key = String(fixture.match_id);
    let group = groups.get(key);
    if (!group) {
      group = { fixture, picks: [] };
      groups.set(key, group);
      order.push(key);
    }
    group.picks.push(pick);
  }
  return order.map((key) => groups.get(key)!);
}

function PickRow({ pick, homeName, awayName }: { pick: Pick; homeName: string; awayName: string }) {
  const modelEdge = String(pick.edge_source) === "model_edge";
  const side = pick.team_side === "away" ? "away" : "home";
  // Nivel 2 de la selección: este mercado no tenía ninguna línea dentro de la
  // banda validada y se publica la más cercana a ella para no dejarlo vacío.
  // Se declara porque no es evidencia del mismo grado que el nivel 1.
  const outsideBand = String(pick.selection) === "outside_band";
  return (
    <div className="market-probability">
      <div>
        <span>{pickLabel(pick, homeName, awayName)}</span>
        <strong>{percentage(pick.observed_rate)}</strong>
      </div>
      <i><b className={side} style={{ width: probabilityWidth(pick.observed_rate) }} /></i>
      <small className="ladder-edge">
        {modelEdge ? "Ventaja del modelo" : "Ventaja de la tasa base"} · el modelo declara{" "}
        {percentage(pick.model_probability)}
        {outsideBand ? " · fuera de la banda objetivo" : ""}
      </small>
    </div>
  );
}

function FixtureCard({ fixture, picks }: FixtureGroup) {
  const homeName = String(fixture.home_team_name || `Equipo ${fixture.home_team_id}`);
  const awayName = String(fixture.away_team_name || `Equipo ${fixture.away_team_id}`);
  const kickoff = typeof fixture.kickoff_ts === "string"
    ? new Date(fixture.kickoff_ts).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" })
    : "—";
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
      <div className="stack" style={{ marginTop: 12 }}>
        {PERIOD_ORDER.map((period) => {
          const rows = picks.filter((pick) => pick.period === period);
          if (!rows.length) return null;
          return (
            <section className="period-market" key={period}>
              <p className="eyebrow">{PERIOD_LABELS[period] ?? period}</p>
              {rows.map((pick, index) => (
                <PickRow
                  key={`${String(pick.market)}-${String(pick.line)}-${index}`}
                  pick={pick} homeName={homeName} awayName={awayName}
                />
              ))}
            </section>
          );
        })}
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
  const fixtureGroups = groupByFixture(picks);
  const provenance = record(query.data?.provenance);
  const unavailable = String(query.data?.status) === "unavailable";

  return (
    <>
      <PageHeader
        eyebrow="ESCALERA AUDITADA · FIABILIDAD VERIFICADA · AUTO 2 MIN"
        title="Mayor probabilidad"
        action={<button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar picks">↻</button>}
      />
      <div className="notice">
        El porcentaje de cada línea es el <strong>acierto histórico real</strong> de esa línea en la
        dirección mostrada, no la probabilidad que declara el modelo. Cada partido muestra los
        mercados de equipo que cubre la escalera auditada -córners, tiros, tiros a puerta y
        tarjetas, completos y de primera mitad-, con la línea más informativa de cada uno: entre 60%
        y 85%. Fuera de ese rango el mercado <strong>no se publica</strong>: ni obviedades (tipo
        &quot;más de 0.5&quot;, que aciertan casi siempre sin informar nada) ni volados casi 50/50.
        Una liga cuyo proveedor no entrega esa estadística tampoco aparece. 1X2, Más de 2.5 y Ambos
        marcan siguen un gate distinto e histórico; ninguno lo superó en ningún tramo.
      </div>
      <div className="metric-grid compact-metrics">
        <Metric label="Picks del día" value={(query.data?.count as number | undefined) ?? "—"} accent />
        <Metric label="Partidos con picks" value={(query.data?.fixtures_with_picks as number | undefined) ?? "—"} />
        <Metric label="Celdas de gol aptas" value={(provenance.eligible_cells as number | undefined) ?? "—"} />
      </div>
      {query.isError ? (
        <StatePanel title="Servicio no disponible" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>
          No se pudo consultar el menú de mayor probabilidad.
        </StatePanel>
      ) : unavailable ? (
        <StatePanel title="Fuentes no disponibles">
          Ni el gate de mercados de gol ni la escalera auditada de mercados de equipo están
          cargados, de modo que no se expone ningún pick.
        </StatePanel>
      ) : query.isLoading ? (
        <LoadingProgress title="Evaluando partidos de hoy" />
      ) : fixtureGroups.length ? (
        <div className="stack">
          {fixtureGroups.map((group) => (
            <FixtureCard key={String(group.fixture.match_id)} fixture={group.fixture} picks={group.picks} />
          ))}
        </div>
      ) : (
        <StatePanel title="Hoy no hay ningún pick disponible">
          No hay partidos con predicción utilizable en el catálogo de hoy, o ninguna liga tiene
          cobertura auditada de córners, tiros o tarjetas. Es un resultado normal y frecuente:
          preferimos no mostrar nada antes que mostrar un pick que no se sostiene.
        </StatePanel>
      )}
    </>
  );
}
