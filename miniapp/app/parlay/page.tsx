"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/components/providers";
import { EntityImage } from "@/components/entity-image";
import { LoadingProgress } from "@/components/loading-progress";
import { PremiumUpsell, usePremium } from "@/components/premium-gate";
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
};

type Leg = Record<string, unknown>;

/** Nombra la pierna por el equipo al que pertenece, no por "local"/"visitante". */
function legLabel(leg: Leg, homeName: string, awayName: string): string {
  const metric = METRICS[String(leg.metric)] ?? String(leg.key).replaceAll("_", " ");
  const side = String(leg.team_side);
  const subject =
    side === "home" ? `${metric} de ${homeName}`
    : side === "away" ? `${metric} de ${awayName}`
    : `${metric} de ambos equipos`;
  const line = leg.line;
  if (typeof line !== "number") return subject;
  return `${subject} · ${String(leg.direction) === "under" ? "Menos de" : "Más de"} ${line}`;
}

/** Identidad estable de una pierna dentro del menú del día. */
function legId(fixtureKey: string, leg: Leg): string {
  return `${fixtureKey}::${String(leg.key)}`;
}

export default function ParlayPage() {
  const premium = usePremium();
  const { csrfToken } = useAuth();
  const [selected, setSelected] = useState<Record<string, { fixtureKey: string; leg: Leg }>>({});

  const query = useQuery({
    queryKey: ["parlay-menu"],
    queryFn: () => api<Record<string, unknown>>(
      `/api/parlay/menu${queryString({ date: today(), limit: 20 })}`),
    refetchInterval: 120_000,
    refetchOnWindowFocus: true,
    enabled: premium,
  });

  const matches = Array.isArray(query.data?.matches)
    ? (query.data.matches as unknown[]).map(record) : [];
  const minLegs = Number(query.data?.min_legs ?? 2);
  const maxLegs = Number(query.data?.max_legs ?? 5);
  const delivery = record(query.data?.delivery);
  const unavailable = String(query.data?.status) === "unavailable";

  const chosen = Object.values(selected);
  const usedFixtures = new Set(chosen.map((item) => item.fixtureKey));

  /**
   * La conjunta se calcula en el cliente para que la cifra responda al toque
   * sin esperar red. El servidor sigue siendo la autoridad: revalida el gate
   * y puede rechazar la combinación, y su ratio de entrega es el que se
   * muestra. Aquí no se decide elegibilidad, sólo se multiplica lo ya elegido.
   */
  const joint = useMemo(
    () => chosen.reduce((acc, item) => acc * Number(item.leg.probability ?? 0), 1),
    [chosen],
  );
  const complete = chosen.length >= minLegs && chosen.length <= maxLegs;

  /**
   * El servidor es la autoridad: revalida cada pierna contra el gate sellado y
   * puede rechazar la combinación. Se consulta sólo cuando la selección está
   * completa, y mientras responde se muestra el cálculo local -misma
   * multiplicación- para que la cifra no parpadee en cada toque.
   */
  const quote = useQuery({
    queryKey: ["parlay-quote", chosen.map((item) => legId(item.fixtureKey, item.leg)).sort()],
    queryFn: () => api<Record<string, unknown>>("/api/parlay/quote", {
      method: "POST",
      body: JSON.stringify({
        legs: chosen.map((item) => ({
          key: String(item.leg.key),
          probability: Number(item.leg.probability),
          fixture_key: item.fixtureKey,
        })),
      }),
    }, csrfToken),
    enabled: complete && Boolean(csrfToken),
    staleTime: 300_000,
  });

  const quoted = record(quote.data);
  const jointShown = typeof quoted.joint_probability === "number"
    ? quoted.joint_probability : joint;
  const ratio = typeof quoted.delivery_ratio === "number"
    ? quoted.delivery_ratio : Number(delivery[String(chosen.length)] ?? NaN);

  function toggle(fixtureKey: string, leg: Leg) {
    const id = legId(fixtureKey, leg);
    setSelected((current) => {
      const next = { ...current };
      if (next[id]) {
        delete next[id];
        return next;
      }
      // Una pierna por partido: elegir otra del mismo partido sustituye a la
      // anterior en vez de fallar. La correlación intra-partido es real y no
      // está modelada, así que multiplicar dos del mismo partido supondría una
      // independencia que el corpus desmiente.
      for (const [key, item] of Object.entries(next)) {
        if (item.fixtureKey === fixtureKey) delete next[key];
      }
      if (Object.keys(next).length >= maxLegs) return current;
      next[id] = { fixtureKey, leg };
      return next;
    });
  }

  return (
    <>
      <PageHeader
        eyebrow="MENÚ EXPERIMENTAL · GATE DE ELEGIBILIDAD · AUTO 2 MIN"
        title="Constructor de Parlays"
        action={
          <button className="icon-button" onClick={() => void query.refetch()}
                  aria-label="Actualizar menú">↻</button>
        }
      />
      <div className="notice">
        Aquí sólo aparecen los mercados que superan un gate de tres filtros:{" "}
        <strong>ventaja real</strong> sobre la tasa base de su liga,{" "}
        <strong>confianza cierta</strong> en el tramo donde se usa, y{" "}
        <strong>estabilidad entre ligas</strong>. No se ordenan por probabilidad:
        de once mercados sólo dos pasan, y algunos de los que declaran cifras más
        altas quedan fuera precisamente por exagerarlas -&quot;Ambos marcan&quot;
        declara 88% y cumple 51%-. Máximo <strong>una pierna por partido</strong>,
        porque dos del mismo partido no son independientes.
      </div>

      <div className="metric-grid compact-metrics">
        <Metric label="Piernas de hoy" value={(query.data?.legs as number | undefined) ?? "—"} accent />
        <Metric label="Partidos" value={(query.data?.matches_with_legs as number | undefined) ?? "—"} />
        <Metric label="Seleccionadas" value={`${chosen.length}/${maxLegs}`} />
      </div>

      {!premium ? (
        <PremiumUpsell
          headline="El Constructor de Parlays es parte de Premium"
          detail="Puedes ver cuántas piernas hay hoy; para abrirlas hace falta el nivel de pago."
        />
      ) : query.isError ? (
        <StatePanel title="Servicio no disponible"
                    action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>
          No se pudo consultar el menú de parlays.
        </StatePanel>
      ) : unavailable ? (
        <StatePanel title="Criterios no disponibles">
          El gate de elegibilidad no está cargado, así que no se expone ninguna pierna.
        </StatePanel>
      ) : query.isLoading ? (
        <LoadingProgress title="Evaluando partidos de hoy" />
      ) : matches.length ? (
        <div className="stack">
          {matches.map((match) => {
            const fixtureKey = String(match.fixture_key);
            const homeName = String(match.home_team_name || `Equipo ${match.home_team_id}`);
            const awayName = String(match.away_team_name || `Equipo ${match.away_team_id}`);
            const legs = Array.isArray(match.legs) ? (match.legs as unknown[]).map(record) : [];
            const kickoff = typeof match.kickoff_ts === "string"
              ? new Date(match.kickoff_ts).toLocaleTimeString(
                  "es-MX", { hour: "2-digit", minute: "2-digit" })
              : "—";
            return (
              <article className="data-panel" key={fixtureKey}>
                <div className="ladder-head">
                  <span>{String(match.league_slug)} · {kickoff}</span>
                  <ShadowBadge />
                </div>
                <div className="market-fixture-teams">
                  <div>
                    <EntityImage source={String(match.home_team_logo || "")} label={homeName} size={34} />
                    <strong>{homeName}</strong>
                  </div>
                  <span>VS</span>
                  <div>
                    <EntityImage source={String(match.away_team_logo || "")} label={awayName} size={34} />
                    <strong>{awayName}</strong>
                  </div>
                </div>
                <div className="stack" style={{ marginTop: 12 }}>
                  {legs.map((leg) => {
                    const id = legId(fixtureKey, leg);
                    const active = Boolean(selected[id]);
                    // Sólo se bloquea cuando el tope está lleno *y* este
                    // partido no aporta ya una pierna: si aporta, tocar otra
                    // la sustituye y no hace falta impedirlo.
                    const blocked = !active
                      && chosen.length >= maxLegs
                      && !usedFixtures.has(fixtureKey);
                    return (
                      <div className="market-probability" key={id}>
                        <div>
                          <span>{legLabel(leg, homeName, awayName)}</span>
                          <strong>{percentage(leg.probability)}</strong>
                        </div>
                        <i>
                          <b className={leg.team_side === "away" ? "away" : "home"}
                             style={{ width: probabilityWidth(leg.probability) }} />
                        </i>
                        <button
                          type="button"
                          className={active ? "primary-button" : "secondary-button"}
                          style={{ justifySelf: "start", marginTop: 6, minHeight: 44 }}
                          onClick={() => toggle(fixtureKey, leg)}
                          disabled={blocked}
                          aria-pressed={active}
                        >
                          {active ? "Quitar del parlay" : blocked ? "Tope alcanzado" : "Añadir al parlay"}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <StatePanel title="Hoy no hay ninguna pierna elegible">
          Ningún partido del catálogo de hoy tiene un mercado que supere el gate. Es un resultado
          normal: preferimos no mostrar nada antes que una pierna que no se sostiene.
        </StatePanel>
      )}

      {chosen.length > 0 ? (
        <article className="data-panel" style={{ marginTop: 18 }}>
          <div className="ladder-head">
            <span>Tu parlay · {chosen.length} {chosen.length === 1 ? "pierna" : "piernas"}</span>
            <ShadowBadge />
          </div>
          <div className="stack" style={{ marginTop: 10 }}>
            {chosen.map(({ fixtureKey, leg }) => (
              <div className="subscription-row" key={legId(fixtureKey, leg)}>
                <span>{String(leg.key).replaceAll("_", " ")}</span>
                <strong>{percentage(leg.probability)}</strong>
              </div>
            ))}
          </div>
          {complete ? (
            <div className="metric-grid compact-metrics" style={{ marginTop: 14 }}>
              <Metric label="Probabilidad conjunta" value={percentage(jointShown)} accent />
              <Metric label="Se cumple" value={Number.isFinite(ratio) ? percentage(ratio) : "—"} />
              <Metric
                label="Ajustada por entrega"
                value={Number.isFinite(ratio) ? percentage(jointShown * ratio) : "—"}
              />
            </div>
          ) : (
            <p className="ladder-caption" style={{ marginTop: 12 }}>
              Añade al menos {minLegs} piernas de partidos distintos para ver la probabilidad
              conjunta.
            </p>
          )}
          {complete && Number.isFinite(ratio) ? (
            <small className="ladder-edge" style={{ display: "block", marginTop: 10 }}>
              La probabilidad conjunta supone independencia entre partidos. Medido fuera de
              muestra, un parlay de {chosen.length} piernas se cumple el{" "}
              {percentage(ratio)} de lo que declara, así que la cifra ajustada es la
              estimación honesta.
            </small>
          ) : null}
          <button
            type="button"
            className="secondary-button"
            style={{ justifySelf: "start", marginTop: 12 }}
            onClick={() => setSelected({})}
          >
            Vaciar selección
          </button>
        </article>
      ) : null}

      <div className="notice" style={{ marginTop: 18 }}>
        {String(query.data?.disclosure
          ?? "Menú experimental sin validación prospectiva. No es un consejo de apuesta.")}
      </div>
    </>
  );
}
