"use client";

import { ShadowBadge } from "@/components/ui";
import { edgeLabel, percentage, probabilityWidth, record } from "@/lib/client-api";
import { metricLabel, teamLabel } from "@/lib/audited-ladder";
import { orderByPeriod, periodLabel } from "@/lib/market-grid";

type Props = {
  rows: unknown;
  homeName: string;
  awayName: string;
};

/**
 * Rejilla pre-match de córners, tiros y tarjetas por periodo -incluye
 * segunda mitad, a diferencia de la escalera auditada.
 *
 * Es una fuente distinta y con menos evidencia que `AuditedLadder`: sus
 * líneas no pasaron el gate de `scripts/run_ladder_audit.py` -calibración
 * medida y ventaja frente a la media de la liga con intervalo de confianza-,
 * así que se etiqueta y se muestra aparte, nunca mezclada con la escalera
 * auditada. El backend ya retira la métrica completa cuando el proveedor no
 * la entrega para esa liga (`src/metric_coverage.py`, DEC-173/182); este
 * componente sólo pinta lo que llega, sin repetir esa lógica en el cliente.
 */
export function MarketGrid({ rows, homeName, awayName }: Props) {
  const groups = orderByPeriod(
    Array.isArray(rows) ? rows.map(record) as {
      key: string; period: string; metric: string; team_side: string;
      expected_count: number; lines: unknown;
    }[] : [],
  );
  if (!groups.length) return null;

  return (
    <article className="model-card">
      <div className="model-card-header"><h3>Rejilla adaptativa por periodo</h3><ShadowBadge /></div>
      <p className="ladder-caption">
        Proyección por primer tiempo, segundo tiempo y partido completo. A diferencia
        de la escalera auditada, estas líneas no verificaron su calibración contra el
        histórico real -son la salida directa del modelo, comparada contra la media de
        la liga como referencia, no una garantía medida.
      </p>
      <div className="stack" style={{ marginTop: 14 }}>
        {groups.map((row, index) => {
          const lines = Array.isArray(row.lines) ? row.lines.map(record) : [];
          if (!lines.length) return null;
          const side = row.team_side === "away" ? "away" : "home";
          const label = teamLabel(String(row.team_side), homeName, awayName);
          const metric = metricLabel(String(row.metric));
          return (
            <div className="ladder-group" key={row.key || index}>
              <div className="ladder-head">
                <span>{periodLabel(row.period)} · {label} · {metric}</span>
                <strong>μ {Number(row.expected_count).toFixed(2)}</strong>
              </div>
              {lines.map((line, lineIndex) => (
                <div className="market-probability" key={`${row.key}-${String(line.line)}-${lineIndex}`}>
                  <div>
                    <span>Más de {String(line.line)} · Menos {percentage(line.under_probability)}</span>
                    <strong>{percentage(line.over_probability)}</strong>
                  </div>
                  <i><b className={side} style={{ width: probabilityWidth(line.over_probability) }} /></i>
                  <small className="ladder-edge">
                    vs media de liga {edgeLabel(line.over_probability, line.baseline_over_probability)}
                  </small>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </article>
  );
}
