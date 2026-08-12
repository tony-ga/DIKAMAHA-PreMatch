import { record } from "@/lib/client-api";

export type AuditedLine = {
  line: number;
  over_probability: number;
  reliability: string;
  observed_rate_historical: number;
  sample_size: number;
};

const METRIC_LABELS: Record<string, string> = {
  shots: "Tiros", shots_on_target: "Tiros a puerta", corners: "Córners",
  yellow_cards: "Tarjetas amarillas",
};

/** Traduce el nombre técnico de la métrica a una etiqueta legible. */
export function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? metric.replaceAll("_", " ");
}

/** Identifica al sujeto de una línea: un equipo o la suma de ambos. */
export function teamLabel(side: string, homeName: string, awayName: string): string {
  if (side === "home") return homeName;
  if (side === "away") return awayName;
  return `${homeName} + ${awayName}`;
}

/** Ordena una escalera de menor a mayor línea, sin mutar el arreglo original. */
export function orderByLine<T extends { line: number }>(lines: T[]): T[] {
  return [...lines].sort((a, b) => a.line - b.line);
}

/**
 * Selecciona las líneas más informativas para la vista compacta por defecto.
 *
 * Un partido típico audita 10-13 líneas por grupo; mostrarlas todas de
 * entrada abruma la pantalla. Se preseleccionan las más cercanas a
 * P(over)=50% -donde una línea distingue mejor entre "sí" y "no"- y se
 * reordenan por línea para que la lectura sea ascendente, igual que la
 * escalera completa.
 */
export function previewLines<T extends { line: number; over_probability: number }>(
  lines: T[], size: number,
): T[] {
  const closestToEven = [...lines].sort(
    (a, b) => Math.abs(a.over_probability - 0.5) - Math.abs(b.over_probability - 0.5));
  return orderByLine(closestToEven.slice(0, size));
}

/** Cuenta cuántas líneas de un grupo tienen ventaja medida del modelo. */
export function countModelEdge(lines: { reliability: string }[]): number {
  return lines.filter((line) => line.reliability === "model_edge").length;
}

/** Extrae y normaliza las filas de la vista auditada desde el payload crudo. */
export function auditedRows(rows: unknown): Record<string, unknown>[] {
  return Array.isArray(rows) ? rows.map(record) : [];
}

/** Extrae y normaliza las líneas de un grupo desde el payload crudo. */
export function auditedLines(row: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(row.lines) ? row.lines.map(record) : [];
}
