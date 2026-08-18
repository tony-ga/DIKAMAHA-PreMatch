"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";

import { usePremium } from "@/components/premium-gate";
import type { Pick } from "@/lib/pick-builder";
import { computeJoint, jointPercentage } from "@/lib/pick-builder";
import {
  MAX_PICKS, serverSnapshot, snapshot, subscribe, togglePick,
} from "@/lib/pick-store";

/** Selecciones vigentes del constructor, sincronizadas entre pantallas. */
export function usePicks(): Pick[] {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}

type Props = {
  /** `null` cuando el mercado no se puede seleccionar; el boton no se pinta. */
  pick: Pick | null;
  /**
   * Palabra que acompana al signo cuando una misma linea ofrece dos
   * selecciones opuestas: dos "+" sin texto no se distinguen entre si.
   */
  caption?: string;
};

/**
 * Boton "+" / "-" que agrega o quita un mercado del Constructor de Picks.
 *
 * Se pinta junto a la probabilidad que el usuario esta leyendo, y guarda esa
 * misma probabilidad: el constructor combina lo que se mostro, no una lectura
 * posterior que podria haber cambiado.
 */
export function PickToggle({ pick, caption }: Props) {
  const picks = usePicks();
  const premium = usePremium();
  if (!pick) return null;
  // El constructor es de pago, así que el "+" no se pinta para quien no lo
  // tiene: dejarlo visible permitiría llenar el constructor y encontrarse el
  // muro sólo al abrirlo, después de haber elegido los mercados. El muro va
  // en `/constructor`, y aquí simplemente no se ofrece la acción.
  if (!premium) return null;
  const selected = picks.some((item) => item.id === pick.id);
  const full = !selected && picks.length >= MAX_PICKS;
  return (
    <button
      type="button"
      className={selected ? "pick-toggle selected" : "pick-toggle"}
      onClick={() => togglePick(pick)}
      disabled={full}
      aria-pressed={selected}
      title={full ? `El constructor admite ${MAX_PICKS} mercados` : undefined}
      aria-label={selected ? `Quitar ${pick.label} del constructor` : `Agregar ${pick.label} al constructor`}
    >
      {caption ? `${caption} ${selected ? "−" : "+"}` : selected ? "−" : "+"}
    </button>
  );
}

/**
 * Aviso flotante con lo que lleva acumulado el constructor.
 *
 * Sin el, agregar un mercado no tiene respuesta visible mas alla del propio
 * boton, y el menu queda escondido detras de una pestana que el usuario no
 * tiene motivo para abrir.
 */
export function PickBuilderBar() {
  const picks = usePicks();
  const premium = usePremium();
  // Sin plan no hay barra: anunciaría un total que su pantalla de destino no
  // le va a mostrar. Las selecciones que hubiera guardado antes siguen en
  // `localStorage` intactas, listas para cuando active Premium.
  if (!premium || !picks.length) return null;
  const joint = computeJoint(picks);
  return (
    <Link href="/constructor" className="pick-bar">
      <span className="pick-bar-count">{picks.length}</span>
      <span>
        {picks.length === 1 ? "1 mercado en el constructor" : `${picks.length} mercados en el constructor`}
      </span>
      <strong>{jointPercentage(joint.probability)}</strong>
    </Link>
  );
}
