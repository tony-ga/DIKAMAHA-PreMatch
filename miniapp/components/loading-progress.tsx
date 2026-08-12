"use client";

import { useEffect, useState } from "react";

/**
 * Indicador de progreso para cálculos que pueden tardar varios segundos.
 *
 * Existe por un reporte real: abrir una predicción "tardaba muchísimo" y no
 * había ninguna señal de que la app siguiera trabajando. La barra es
 * deliberadamente indeterminada (un segmento que se desliza, no un
 * porcentaje) porque el servidor no expone progreso real de una inferencia
 * causal — inventar un número que suba solo sería un patrón engañoso. Lo que
 * sí puede ser honesto es el tiempo transcurrido y un mensaje que reconozca
 * cuando la espera se alarga, para que el usuario sepa que sigue en curso y
 * no que la app se congeló.
 */

const STAGES = [
  { afterSeconds: 0, message: "Resolviendo equipos y aplicando el snapshot causal anterior al kickoff." },
  { afterSeconds: 4, message: "Calculando Dixon-Coles y Kalman para este partido." },
  { afterSeconds: 10, message: "Está tardando más de lo habitual. Sigue en curso, no hace falta recargar." },
  { afterSeconds: 20, message: "Casi listo. Un partido con historial extenso puede tardar un poco más." },
] as const;

function currentStage(elapsedSeconds: number): string {
  let message: string = STAGES[0].message;
  for (const stage of STAGES) {
    if (elapsedSeconds >= stage.afterSeconds) message = stage.message;
  }
  return message;
}

export function LoadingProgress({ title }: { title: string }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setElapsed((Date.now() - start) / 1000), 250);
    return () => clearInterval(id);
  }, []);

  return (
    <section className="state-panel" role="status" aria-live="polite">
      <div className="progress-track"><div className="progress-fill" /></div>
      <h2>{title}</h2>
      <div className="muted">{currentStage(elapsed)}</div>
    </section>
  );
}
