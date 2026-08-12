"use client";

import { useState } from "react";

import { ShadowBadge } from "@/components/ui";
import { countLabel, percentage, probabilityWidth } from "@/lib/client-api";
import {
  auditedLines, auditedRows, countModelEdge, metricLabel, orderByLine,
  previewLines, teamLabel,
} from "@/lib/audited-ladder";

const PERIODS = [
  { key: "first_half", label: "Primer tiempo" },
  { key: "full_match", label: "Partido completo" },
];

const PREVIEW_SIZE = 4;

type Props = {
  rows: unknown;
  homeName: string;
  awayName: string;
};

/**
 * Escalera completa de córners, tiros y tarjetas, auditada contra el
 * histórico real -no la rejilla de tres líneas centradas sin verificar.
 *
 * Cada línea que aparece aquí pasó dos gates medidos por
 * `scripts/run_ladder_audit.py`: está calibrada (si el modelo declara una
 * probabilidad, la cumple) y tiene fiabilidad demostrada, por ventaja del
 * modelo sobre la media de la liga o porque la línea es predecible por sí
 * sola. Ver `docs/objetivo_auditoria_modelos_v1.md`.
 */
export function AuditedLadder({ rows, homeName, awayName }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const groups = auditedRows(rows);
  if (!groups.length) return null;

  return (
    <article className="model-card">
      <div className="model-card-header"><h3>Escalera auditada</h3><ShadowBadge /></div>
      <p className="ladder-caption">
        Sólo se muestran líneas verificadas contra partidos reales: calibradas y con
        fiabilidad medida. &quot;Modelo&quot; significa que conocer a estos equipos mejora la
        probabilidad frente a la media de la liga; &quot;liga&quot; significa que la línea es
        fiable pero no distingue a estos equipos en particular.
      </p>
      <div className="stack" style={{ marginTop: 14 }}>
        {PERIODS.map((period) => {
          const periodGroups = groups.filter((row) => row.period === period.key);
          if (!periodGroups.length) return null;
          return (
            <section className="period-market" key={`audited-${period.key}`}>
              <p className="eyebrow">{period.label}</p>
              {periodGroups.map((row, index) => {
                const key = `${period.key}-${String(row.key)}-${index}`;
                const lines = auditedLines(row).map((line) => ({
                  line: Number(line.line),
                  over_probability: Number(line.over_probability),
                  reliability: String(line.reliability),
                  observed_rate_historical: Number(line.observed_rate_historical),
                  sample_size: Number(line.sample_size),
                }));
                if (!lines.length) return null;
                const side = row.team_side === "away" ? "away" : "home";
                const label = teamLabel(String(row.team_side), homeName, awayName);
                const metric = metricLabel(String(row.metric));
                const edgeCount = countModelEdge(lines);
                const isExpanded = Boolean(expanded[key]);
                const visible = isExpanded ? orderByLine(lines) : previewLines(lines, PREVIEW_SIZE);
                return (
                  <div className="ladder-group" key={key}>
                    <div className="ladder-head">
                      <span>{label} · {metric}</span>
                      <strong>μ {countLabel(row.expected_count)}</strong>
                    </div>
                    <small className="ladder-edge">
                      {edgeCount > 0
                        ? `${edgeCount} de ${lines.length} líneas con ventaja del modelo`
                        : `${lines.length} líneas fiables según la media de la liga`}
                    </small>
                    {visible.map((line, lineIndex) => {
                      const modelEdge = line.reliability === "model_edge";
                      return (
                        <div className="market-probability" key={`${key}-${String(line.line)}-${lineIndex}`}>
                          <div>
                            <span>Más de {String(line.line)} · {modelEdge ? "modelo" : "liga"}</span>
                            <strong>{percentage(line.over_probability)}</strong>
                          </div>
                          <i><b className={side} style={{ width: probabilityWidth(line.over_probability) }} /></i>
                          <small className="ladder-edge">
                            histórico {percentage(line.observed_rate_historical)} sobre {String(line.sample_size)} partidos
                          </small>
                        </div>
                      );
                    })}
                    {lines.length > PREVIEW_SIZE ? (
                      <button
                        type="button"
                        className="secondary-button"
                        style={{ justifySelf: "start", marginTop: 4 }}
                        onClick={() => setExpanded((state) => ({ ...state, [key]: !isExpanded }))}
                      >
                        {isExpanded ? "Mostrar menos" : `Ver las ${lines.length} líneas`}
                      </button>
                    ) : null}
                  </div>
                );
              })}
            </section>
          );
        })}
      </div>
    </article>
  );
}
