"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { api, percentage, record } from "@/lib/client-api";

const OFFICIAL_LABELS: Array<[string, string]> = [
  ["one_x_two", "Resultado (1X2)"],
  ["over_2_5", "Más de 2.5 goles"],
  ["btts", "Ambos marcan"],
];

function MarketSummary({ label, value }: { label: string; value: unknown }) {
  const market = record(value);
  const hits = Number(market.hits ?? 0);
  const total = Number(market.total ?? 0);
  if (!market.sufficient_sample) {
    const missing = Number(market.missing_for_rate ?? 0);
    return (
      <div className="ladder-group">
        <div className="ladder-head"><span>{label}</span><strong>{hits}/{total}</strong></div>
        <small className="ladder-edge">
          Muestra insuficiente para un porcentaje fiable. Faltan {missing} partidos verificados.
        </small>
      </div>
    );
  }
  const interval = Array.isArray(market.interval_95) ? market.interval_95 : [];
  return (
    <div className="ladder-group">
      <div className="ladder-head">
        <span>{label}</span>
        <strong>{percentage(market.rate)}</strong>
      </div>
      <div className="subscription-row">
        <span>Aciertos verificados</span><strong>{hits}/{total}</strong>
      </div>
      <small className="ladder-edge">
        Entre {percentage(interval[0])} y {percentage(interval[1])} · referencia {percentage(market.baseline_rate)}
      </small>
    </div>
  );
}

function today(): string {
  return new Date().toISOString().slice(0, 10).replaceAll("-", "");
}

function MatchRow({ value }: { value: unknown }) {
  const row = record(value);
  const verdicts = record(row.official_verdicts);
  const kickoff = typeof row.kickoff_ts === "string"
    ? new Date(row.kickoff_ts).toLocaleDateString("es-MX", { day: "2-digit", month: "short" })
    : "—";
  return (
    <div className="ladder-group">
      <div className="ladder-head">
        <span>{String(row.home_team_name)} {String(row.score_home)}–{String(row.score_away)} {String(row.away_team_name)}</span>
        <strong>{kickoff}</strong>
      </div>
      {OFFICIAL_LABELS.map(([key, label]) => {
        const verdict = record(verdicts[key]);
        if (verdict.hit === undefined) return null;
        return (
          <div className="subscription-row" key={key}>
            <span>{label} · {String(verdict.predicted)}</span>
            <strong>{verdict.hit ? "✅" : "❌"}</strong>
          </div>
        );
      })}
      <small className="ladder-edge">Predicción congelada · {String(row.prediction_hash ?? "")}</small>
    </div>
  );
}

export function DailyTrackRecord() {
  const dateParam = today();
  const query = useQuery({
    queryKey: ["track-record-daily", dateParam],
    queryFn: () =>
      api<Record<string, unknown>>(`/api/track-record/daily?date=${dateParam}`),
    staleTime: 60_000,
  });
  if (query.isLoading) {
    return <StatePanel title="Cargando resultados de hoy">Leyendo los partidos liquidados hoy.</StatePanel>;
  }
  const payload = record(query.data);
  if (query.isError || payload.status !== "available") {
    return null;
  }
  const matches = Array.isArray(payload.matches) ? payload.matches : [];
  if (!matches.length) {
    return (
      <article className="model-card">
        <div className="model-card-header"><h3>Resultados de hoy</h3></div>
        <p className="ladder-caption">Todavía no se liquidó ningún partido de hoy.</p>
      </article>
    );
  }
  const oneXTwo = record(record(payload.official).one_x_two);
  const hits = Number(oneXTwo.hits ?? 0);
  return (
    <article className="model-card">
      <div className="model-card-header"><h3>Resultados de hoy</h3></div>
      <p className="ladder-caption">
        {hits}/{matches.length} resultados 1X2 acertados hoy. Se muestran todos los partidos
        liquidados, acertados y no acertados.
      </p>
      <div className="stack" style={{ marginTop: 14 }}>
        {matches.map((match, index) => (
          <MatchRow key={String(record(match).fixture_key ?? index)} value={match} />
        ))}
      </div>
    </article>
  );
}

export function TrackRecord() {
  const query = useQuery({
    queryKey: ["track-record"],
    queryFn: () => api<Record<string, unknown>>("/api/track-record?window=60"),
    staleTime: 300_000,
  });
  if (query.isLoading) {
    return <StatePanel title="Cargando desempeño">Leyendo los partidos ya verificados.</StatePanel>;
  }
  const payload = record(query.data);
  if (query.isError || payload.status !== "available") {
    return (
      <StatePanel title="Historial no disponible">
        Todavía no hay partidos verificados publicados, o el registro no está configurado.
      </StatePanel>
    );
  }
  const window = record(payload.window);
  const official = record(payload.official);
  const shadow = record(payload.shadow);
  const shadowMarkets = record(shadow.markets);
  const matches = Array.isArray(payload.matches) ? payload.matches : [];
  if (!matches.length) {
    return (
      <StatePanel title="Sin partidos verificados">
        El historial empieza vacío y sólo suma partidos cuya predicción se congeló antes del kickoff.
      </StatePanel>
    );
  }
  return (
    <>
      <PageHeader eyebrow="DESEMPEÑO VERIFICADO" title="Historial de aciertos" />
      <div className="stack">
        <article className="model-card">
          <div className="model-card-header"><h3>Mercados oficiales</h3><ShadowBadge official /></div>
          <p className="ladder-caption">
            Sobre {String(window.available)} partidos verificados. Se cuentan aciertos y fallos del periodo completo.
          </p>
          <div className="stack" style={{ marginTop: 14 }}>
            {OFFICIAL_LABELS.map(([key, label]) => (
              <MarketSummary key={key} label={label} value={official[key]} />
            ))}
          </div>
        </article>
        {Object.keys(shadowMarkets).length ? (
          <article className="model-card">
            <div className="model-card-header"><h3>Mercados experimentales</h3><ShadowBadge /></div>
            <p className="ladder-caption">
              Sin validación confirmatoria. Se muestra el conteo, no un porcentaje, porque estas líneas no están promovidas.
            </p>
            <div className="stack" style={{ marginTop: 14 }}>
              {Object.entries(shadowMarkets).map(([key, value]) => {
                const market = record(value);
                return (
                  <div className="subscription-row" key={key}>
                    <span>{key.replaceAll("_", " ")}</span>
                    <strong>{String(market.hits ?? 0)}/{String(market.total ?? 0)}</strong>
                  </div>
                );
              })}
            </div>
          </article>
        ) : null}
        <article className="model-card">
          <div className="model-card-header"><h3>Partidos verificados</h3></div>
          <div className="stack" style={{ marginTop: 14 }}>
            {matches.map((match, index) => (
              <MatchRow key={String(record(match).fixture_key ?? index)} value={match} />
            ))}
          </div>
        </article>
        <div className="notice">{String(payload.disclosure ?? "")}</div>
      </div>
    </>
  );
}
