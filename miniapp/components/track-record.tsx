"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  DailyTrendChart, LeagueRateChart, MarketRateChart, ShadowRateChart,
} from "@/components/track-record-charts";
import { Metric, PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { api, percentage, record } from "@/lib/client-api";
import {
  SHADOW_PREVIEW_SIZE, channelDateParam, highProbabilityPickLabel,
  highProbabilityPicks, shadowMarketLabel, shadowMatchEntries, shadowSummary,
} from "@/lib/track-record";
import {
  dailyHitRateSeries, leagueHitRateSeries, officialMarketRateSeries,
  shadowMarketRateSeries,
} from "@/lib/track-record-charts";

const OFFICIAL_LABELS: Array<[string, string]> = [
  ["one_x_two", "Resultado (1X2)"],
  ["over_2_5", "Más de 2.5 goles"],
  ["btts", "Ambos marcan"],
];

function VerdictMark({ hit }: { hit: boolean }) {
  return <strong className={hit ? "verdict-hit" : "verdict-miss"}>{hit ? "Acierto" : "Fallo"}</strong>;
}

function PickStatusMark({ status }: { status: string }) {
  if (status === "pending") return <strong className="muted">Pendiente</strong>;
  return <VerdictMark hit={status === "hit"} />;
}

/**
 * Picks del menú de "Mayor probabilidad" (Fase 123) dentro de Aciertos.
 *
 * Cronológico y sin filtrar por estado -incluye "Pendiente" para un pick
 * congelado cuyo partido todavía no liquidó-, mismo principio de DEC-158/161
 * que ya gobierna `MatchRow`. Reutiliza exactamente el `market`/`direction`/
 * `metric`/`team_side`/`period`/`line` que el menú congeló.
 */
function HighProbabilityPicks({ value }: { value: unknown }) {
  const block = record(value);
  const picks = highProbabilityPicks(value);
  if (block.status !== "available" || !picks.length) return null;
  const summary = record(block.summary);
  return (
    <article className="model-card">
      <div className="model-card-header"><h3>Mayor probabilidad</h3><ShadowBadge /></div>
      <p className="ladder-caption">
        Picks del menú "Mayor probabilidad" congelados antes del kickoff, con el mismo
        mercado que se mostró entonces. Cronológico, incluye pendientes y fallos.
      </p>
      <div className="metric-grid compact-metrics" style={{ marginTop: 10 }}>
        <Metric label="Aciertos" value={`${Number(summary.hits ?? 0)}/${Number(summary.settled ?? 0)}`} accent />
        <Metric label="Pendientes" value={String(summary.pending ?? 0)} />
      </div>
      <div className="stack" style={{ marginTop: 14 }}>
        {picks.map((entry) => {
          const homeName = String(entry.home_team_name ?? "");
          const awayName = String(entry.away_team_name ?? "");
          const matchLabel = homeName && awayName ? `${homeName} vs ${awayName}` : entry.league_slug;
          return (
            <div className="ladder-group" key={String(entry.pick_key)}>
              <div className="ladder-head">
                <span>{highProbabilityPickLabel(entry, homeName, awayName)}</span>
                <PickStatusMark status={String(entry.status)} />
              </div>
              <small className="ladder-edge">{String(matchLabel)}</small>
            </div>
          );
        })}
      </div>
    </article>
  );
}

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

function MatchRow({ value }: { value: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const row = record(value);
  const verdicts = record(row.official_verdicts);
  const homeName = String(row.home_team_name ?? "");
  const awayName = String(row.away_team_name ?? "");
  const shadowEntries = shadowMatchEntries(row.shadow_verdicts);
  const visibleShadow = expanded ? shadowEntries : shadowEntries.slice(0, SHADOW_PREVIEW_SIZE);
  const kickoff = typeof row.kickoff_ts === "string"
    ? new Date(row.kickoff_ts).toLocaleDateString("es-MX", { day: "2-digit", month: "short" })
    : "—";
  return (
    <div className="ladder-group">
      <div className="ladder-head">
        <span>{homeName} {String(row.score_home)}–{String(row.score_away)} {awayName}</span>
        <strong>{kickoff}</strong>
      </div>
      {OFFICIAL_LABELS.map(([key, label]) => {
        const verdict = record(verdicts[key]);
        if (verdict.hit === undefined) return null;
        return (
          <div className="subscription-row" key={key}>
            <span>{label} · {String(verdict.predicted)}</span>
            <VerdictMark hit={Boolean(verdict.hit)} />
          </div>
        );
      })}
      {visibleShadow.map((entry) => (
        <div className="subscription-row" key={entry.key}>
          <span>{shadowMarketLabel(entry.key, homeName, awayName)} · {String(entry.predicted)}</span>
          <VerdictMark hit={Boolean(entry.hit)} />
        </div>
      ))}
      {shadowEntries.length > SHADOW_PREVIEW_SIZE ? (
        <button
          type="button"
          className="secondary-button"
          style={{ justifySelf: "start", marginTop: 4 }}
          onClick={() => setExpanded((state) => !state)}
        >
          {expanded ? "Mostrar menos" : `Ver las ${shadowEntries.length} líneas`}
        </button>
      ) : null}
      <small className="ladder-edge">Predicción congelada · {String(row.prediction_hash ?? "")}</small>
    </div>
  );
}

export function DailyTrackRecord() {
  const dateParam = channelDateParam();
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
  const highProbability = <HighProbabilityPicks value={payload.high_probability} />;
  if (!matches.length) {
    // El canal (predicciones 1X2/Más de 2.5/Ambos marcan) puede no haber
    // liquidado nada hoy mientras el menú de "Mayor probabilidad" sí tiene
    // picks congelados o ya resueltos -son dos ciclos independientes-, así
    // que la ventana no debe quedar en blanco sólo porque uno de los dos
    // esté vacío.
    return (
      <div className="stack">
        <article className="model-card">
          <div className="model-card-header"><h3>Resultados de hoy</h3></div>
          <p className="ladder-caption">Todavía no se liquidó ningún partido de hoy.</p>
        </article>
        {highProbability}
      </div>
    );
  }
  const official = record(payload.official);
  const shadow = shadowSummary(payload.shadow);
  return (
    <div className="stack">
      <article className="model-card">
        <div className="model-card-header"><h3>Resultados de hoy</h3></div>
        <p className="ladder-caption">
          Todos los partidos liquidados hoy, acertados y no acertados, con todos los mercados
          y líneas calculadas para cada uno.
        </p>
        <div className="metric-grid compact-metrics" style={{ marginTop: 10 }}>
          {OFFICIAL_LABELS.map(([key, label]) => {
            const market = record(official[key]);
            return (
              <Metric
                key={key}
                label={label}
                value={`${Number(market.hits ?? 0)}/${Number(market.total ?? 0)}`}
                accent={key === "one_x_two"}
              />
            );
          })}
          {shadow.total > 0 ? (
            <Metric label="Córners, tiros y tarjetas" value={`${shadow.hits}/${shadow.total}`} />
          ) : null}
        </div>
        <div className="stack" style={{ marginTop: 14 }}>
          {matches.map((match, index) => (
            <MatchRow key={String(record(match).fixture_key ?? index)} value={match} />
          ))}
        </div>
      </article>
      {highProbability}
    </div>
  );
}

export function TrackRecord() {
  const query = useQuery({
    queryKey: ["track-record"],
    queryFn: () => api<Record<string, unknown>>("/api/track-record?window=200"),
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
  const marketRates = officialMarketRateSeries(official);
  const dailyRates = dailyHitRateSeries(matches);
  const leagueRates = leagueHitRateSeries(matches);
  const shadowRates = shadowMarketRateSeries(shadowMarkets);
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
          {marketRates.length ? (
            <>
              <p className="ladder-caption" style={{ marginTop: 14 }}>
                Acertado frente a la referencia -el reparto base de esa liga, no una cuota de apuesta-.
              </p>
              <MarketRateChart points={marketRates} />
            </>
          ) : null}
        </article>
        {dailyRates.length >= 2 || leagueRates.length ? (
          <article className="model-card">
            <div className="model-card-header"><h3>Tendencia</h3></div>
            {dailyRates.length >= 2 ? (
              <>
                <p className="ladder-caption">Acierto agregado por día, sobre los tres mercados oficiales juntos.</p>
                <DailyTrendChart points={dailyRates} />
              </>
            ) : null}
            {leagueRates.length ? (
              <>
                <p className="ladder-caption" style={{ marginTop: dailyRates.length >= 2 ? 18 : 0 }}>
                  Acierto por liga, ligas con más partidos verificados primero.
                </p>
                <LeagueRateChart points={leagueRates} />
              </>
            ) : null}
          </article>
        ) : null}
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
            {shadowRates.length ? <ShadowRateChart points={shadowRates} /> : null}
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
        <HighProbabilityPicks value={payload.high_probability} />
        <div className="notice">{String(payload.disclosure ?? "")}</div>
      </div>
    </>
  );
}
