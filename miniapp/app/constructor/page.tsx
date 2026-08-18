"use client";

import Link from "next/link";

import { EntityImage } from "@/components/entity-image";
import { usePicks } from "@/components/pick-toggle";
import { PremiumUpsell, usePremium } from "@/components/premium-gate";
import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { percentage } from "@/lib/client-api";
import type { MatchBreakdown, Pick } from "@/lib/pick-builder";
import { computeJoint, fairOdds, jointPercentage } from "@/lib/pick-builder";
import { clearPicks, MAX_PICKS, removePick } from "@/lib/pick-store";

function kickoffLabel(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return date.toLocaleString("es-MX", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function PickRow({ pick }: { pick: Pick }) {
  return (
    <div className="market-probability">
      <div>
        <span>{pick.label}</span>
        <strong>{percentage(pick.probability)}</strong>
      </div>
      <button
        type="button"
        className="pick-toggle selected"
        onClick={() => removePick(pick.id)}
        aria-label={`Quitar ${pick.label}`}
      >
        −
      </button>
    </div>
  );
}

function MatchCard({ breakdown }: { breakdown: MatchBreakdown }) {
  const { match, blocks } = breakdown;
  return (
    <article className="data-panel">
      <div className="ladder-head">
        <span>{match.league} · {kickoffLabel(match.kickoff)}</span>
        <strong>{jointPercentage(breakdown.probability)}</strong>
      </div>
      <div className="market-fixture-teams">
        <div><EntityImage source={match.homeLogo ?? ""} label={match.homeName} size={30} /><strong>{match.homeName}</strong></div>
        <span>VS</span>
        <div><EntityImage source={match.awayLogo ?? ""} label={match.awayName} size={30} /><strong>{match.awayName}</strong></div>
      </div>
      <div className="stack" style={{ marginTop: 12 }}>
        {blocks.map((block) => (
          <section className="period-market" key={block.key}>
            <p className="eyebrow">
              {block.label} · {jointPercentage(block.probability)}
              {block.exact ? "" : " · aproximado"}
            </p>
            {block.picks.map((pick) => <PickRow key={pick.id} pick={pick} />)}
            {block.note ? <small className="ladder-edge">{block.note}</small> : null}
          </section>
        ))}
      </div>
      {breakdown.degraded ? (
        <small className="ladder-edge">
          Este partido se calculó de forma degradada ({breakdown.degraded}).
        </small>
      ) : null}
    </article>
  );
}

export default function PickBuilderPage() {
  const picks = usePicks();
  const premium = usePremium();
  const joint = computeJoint(picks);
  const multipleMatches = joint.matches.length > 1;

  if (!premium) {
    // Se conserva la cabecera y se explica qué hace el constructor antes del
    // muro: quien llega aquí desde la barra inferior necesita saber qué es lo
    // que estaría activando, no sólo que no puede pasar.
    return (
      <>
        <PageHeader eyebrow="CONSTRUCTOR" title="Construir pick" />
        <PremiumUpsell
          headline="El constructor de picks es parte de Premium"
          detail={
            "Combina los mercados que elijas -de uno o varios partidos- en una "
            + "sola probabilidad conjunta, calculada sobre la matriz de "
            + "marcadores del modelo."
          }
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow={`CONSTRUCTOR · HASTA ${MAX_PICKS} MERCADOS`}
        title="Construir pick"
        action={picks.length
          ? <button className="icon-button" onClick={() => clearPicks()} aria-label="Vaciar el constructor">✕</button>
          : undefined}
      />
      <div className="notice">
        Ve a cualquier predicción <strong>pre-match</strong>, elige los mercados que te interesen y
        pulsa <strong>+</strong> para agregarlos o <strong>−</strong> para quitarlos. Puedes mezclar
        mercados de <strong>partidos distintos</strong>. Aquí se publica una única probabilidad de
        que ocurran <strong>todos</strong> a la vez.
      </div>
      {!picks.length ? (
        <StatePanel
          title="Todavía no has agregado ningún mercado"
          action={<Link href="/predictions" className="primary-button">Ver predicciones</Link>}
        >
          Abre una predicción pre-match y pulsa el <strong>+</strong> que aparece junto a cada
          mercado: 1X2, Más de 2.5, Ambos marcan y cualquier línea de córners, tiros o tarjetas.
        </StatePanel>
      ) : (
        <div className="stack">
          <article className="model-card">
            <div className="model-card-header"><h3>Probabilidad conjunta</h3><ShadowBadge /></div>
            <div className="joint-headline">
              <strong>{jointPercentage(joint.probability)}</strong>
              <small>
                {picks.length} {picks.length === 1 ? "mercado" : "mercados"} ·{" "}
                {joint.matches.length} {joint.matches.length === 1 ? "partido" : "partidos"} ·
                cuota justa {fairOdds(joint.probability)}
              </small>
            </div>
            {joint.impossible ? (
              <p className="ladder-caption">
                Las selecciones se excluyen entre sí: no existe ningún resultado que las cumpla
                todas a la vez, así que la probabilidad conjunta es exactamente cero.
              </p>
            ) : (
              <p className="ladder-caption">
                {multipleMatches
                  ? "Entre partidos distintos se multiplica: el motor calcula cada partido por separado, sin estado compartido. "
                  : ""}
                Dentro de un mismo partido, los mercados de gol se resuelven sobre la matriz de
                marcadores del modelo -no multiplicando-, y dos líneas de la misma variable
                (por ejemplo dos líneas de córners del mismo equipo y periodo) recortan el
                intervalo en vez de multiplicarse.
              </p>
            )}
            {joint.warnings.length ? (
              <ul className="joint-warnings">
                {joint.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
              </ul>
            ) : null}
          </article>
          {joint.matches.map((breakdown) => (
            <MatchCard key={breakdown.match.matchId} breakdown={breakdown} />
          ))}
          <div className="notice">
            Estas probabilidades son analíticas y <strong>shadow</strong>: no son cuotas, no
            incorporan margen de casa y no constituyen recomendación de apuesta. El constructor no
            congela ni liquida nada, así que estos picks no entran en el historial de aciertos.
          </div>
        </div>
      )}
    </>
  );
}
