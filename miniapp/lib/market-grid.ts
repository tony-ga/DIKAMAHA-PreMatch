const PERIOD_LABELS: Record<string, string> = {
  first_half: "Primer tiempo",
  second_half: "Segundo tiempo",
  full_match: "Partido completo",
};

/** Traduce el periodo técnico a una etiqueta legible. */
export function periodLabel(period: string): string {
  return PERIOD_LABELS[period] ?? period.replaceAll("_", " ");
}

/**
 * Orden de lectura de la rejilla: primero por periodo (1T, 2T, completo),
 * luego alfabético por clave para que la posición de cada tarjeta sea
 * estable entre refrescos en vez de depender del orden que devuelva el
 * backend.
 */
const PERIOD_ORDER: Record<string, number> = {
  first_half: 0, second_half: 1, full_match: 2,
};

export function orderByPeriod<T extends { period: string; key: string }>(
  rows: T[],
): T[] {
  return [...rows].sort((a, b) => {
    const delta = (PERIOD_ORDER[a.period] ?? 99) - (PERIOD_ORDER[b.period] ?? 99);
    return delta !== 0 ? delta : a.key.localeCompare(b.key);
  });
}
