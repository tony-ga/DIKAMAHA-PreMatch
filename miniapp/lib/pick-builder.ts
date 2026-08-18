/**
 * Constructor de Picks: probabilidad conjunta de varios mercados ya
 * publicados, de uno o de varios partidos (DEC-208).
 *
 * El calculo no inventa un modelo nuevo. Reusa exactamente lo que la
 * prediccion pre-match ya publica y lo combina en tres regimenes, en orden de
 * menor a mayor supuesto:
 *
 * 1. Dos lineas sobre **la misma variable** -mismo grupo metrica/lado/periodo
 *    de la escalera- no son dos eventos: son uno. Se resuelven de forma exacta
 *    sobre la propia escalera, sin ningun supuesto.
 * 2. Los mercados de gol de **un mismo partido** (1X2, Mas de 2.5, Ambos
 *    marcan) se resuelven sumando la masa de la matriz de marcadores sobre las
 *    celdas que cumplen todas las condiciones a la vez. Tambien exacto: la
 *    matriz es la conjunta del modelo.
 * 3. Todo lo demas -grupos distintos del mismo partido, y partidos distintos-
 *    se multiplica. Entre partidos la independencia es una propiedad de la
 *    cadena servida, que calcula cada partido por separado. Entre grupos del
 *    mismo partido es el unico supuesto real del constructor, y se declara.
 *
 * Ver `docs/decision_log.md`, DEC-208.
 */

export type GoalMarketKey =
  | "home"
  | "draw"
  | "away"
  | "over_2_5"
  | "under_2_5"
  | "btts_yes"
  | "btts_no";

/**
 * Todo lo necesario para reconstruir la conjunta de goles del partido.
 *
 * `lambdaHome`, `lambdaAway` y `tau` vienen del payload de
 * `/v1/predict/upcoming` (`lambda_home`, `lambda_away`, `audit.tau_dc`).
 * `tau` se publica precisamente para poder reconstruir la matriz con la que
 * se derivaron los mercados; ver `src/official_goal_chain.py`. Las cinco
 * probabilidades son las que el usuario ve en pantalla, y son las que el
 * ajuste de marginales impone sobre la matriz.
 */
export type GoalContext = {
  lambdaHome: number;
  lambdaAway: number;
  tau: number;
  probabilityHome: number;
  probabilityDraw: number;
  probabilityAway: number;
  probabilityOver25: number;
  probabilityBtts: number;
};

export type MatchRef = {
  matchId: string;
  league: string;
  homeTeamId: string;
  awayTeamId: string;
  kickoff: string;
  homeName: string;
  awayName: string;
  homeLogo?: string;
  awayLogo?: string;
};

export type GoalPick = {
  id: string;
  kind: "goal";
  match: MatchRef;
  label: string;
  probability: number;
  market: GoalMarketKey;
  goals: GoalContext;
  addedAt: number;
};

export type CountPick = {
  id: string;
  kind: "count";
  match: MatchRef;
  label: string;
  /** La probabilidad mostrada: over o under segun `direction`. */
  probability: number;
  /** Identidad de la variable aleatoria: metrica + lado + periodo. */
  groupKey: string;
  groupLabel: string;
  line: number;
  direction: "over" | "under";
  /** P(X > line). Se guarda siempre, tambien en las selecciones "under". */
  overProbability: number;
  /** Escalera de procedencia: `audited` o `grid`. Solo informativo. */
  source: string;
  addedAt: number;
};

export type Pick = GoalPick | CountPick;

export type BlockBreakdown = {
  key: string;
  label: string;
  probability: number;
  /** `true` cuando el bloque no usa ningun supuesto de independencia. */
  exact: boolean;
  note: string;
  picks: Pick[];
};

export type MatchBreakdown = {
  match: MatchRef;
  probability: number;
  blocks: BlockBreakdown[];
  /** Motivo por el que el partido cayo a un calculo degradado, si aplica. */
  degraded: string;
};

export type JointResult = {
  probability: number;
  matches: MatchBreakdown[];
  /** Producto ingenuo de todas las marginales, solo para contraste. */
  independentProduct: number;
  impossible: boolean;
  warnings: string[];
};

/** Tamano de la rejilla de marcadores; el mismo que sirve el backend. */
export const MAX_GOALS = 12;

const RAKING_ITERATIONS = 400;
const RAKING_TOLERANCE = 1e-13;

/* ------------------------------------------------------------------ *
 * Matriz de marcadores                                                *
 * ------------------------------------------------------------------ */

function factorial(value: number): number {
  let result = 1;
  for (let index = 2; index <= value; index += 1) result *= index;
  return result;
}

function poissonMass(rate: number, goals: number): number {
  return (Math.exp(-rate) * rate ** goals) / factorial(goals);
}

/**
 * Correccion Dixon-Coles de marcadores bajos, identica a
 * `low_score_tau` en `src/dixon_coles_v1.py`.
 */
function lowScoreTau(
  home: number, away: number, lambdaHome: number, lambdaAway: number, tau: number,
): number {
  if (home === 0 && away === 0) return Math.max(1e-9, 1 - lambdaHome * lambdaAway * tau);
  if (home === 1 && away === 0) return Math.max(1e-9, 1 + lambdaAway * tau);
  if (home === 0 && away === 1) return Math.max(1e-9, 1 + lambdaHome * tau);
  if (home === 1 && away === 1) return Math.max(1e-9, 1 - tau);
  return 1;
}

/**
 * Reconstruye la matriz conjunta de goles, normalizada.
 *
 * Replica `poisson_matrix` de `src/kalman_v2.py` con el mismo tamano de
 * rejilla y la misma correccion de marcadores bajos.
 */
export function scoreMatrix(context: GoalContext, maxGoals: number = MAX_GOALS): number[][] {
  const { lambdaHome, lambdaAway, tau } = context;
  if (!Number.isFinite(lambdaHome) || !Number.isFinite(lambdaAway)) {
    throw new Error("lambdas_not_finite");
  }
  if (lambdaHome <= 0 || lambdaAway <= 0) throw new Error("lambdas_not_positive");
  const homeMass = Array.from({ length: maxGoals + 1 }, (_, goals) => poissonMass(lambdaHome, goals));
  const awayMass = Array.from({ length: maxGoals + 1 }, (_, goals) => poissonMass(lambdaAway, goals));
  const grid = homeMass.map((mass, home) => awayMass.map((other, away) => {
    const correction = Number.isFinite(tau) && tau !== 0
      ? lowScoreTau(home, away, lambdaHome, lambdaAway, tau)
      : 1;
    return mass * other * correction;
  }));
  const total = grid.reduce((sum, row) => sum + row.reduce((acc, cell) => acc + cell, 0), 0);
  if (!Number.isFinite(total) || total <= 0) throw new Error("score_matrix_invalid");
  return grid.map((row) => row.map((cell) => cell / total));
}

/* ------------------------------------------------------------------ *
 * Ajuste a las marginales publicadas                                  *
 * ------------------------------------------------------------------ */

type Constraint = {
  /** Clase de cada celda dentro de la particion. */
  classOf: (home: number, away: number) => number;
  /** Masa objetivo de cada clase, en el mismo orden. */
  targets: number[];
};

function normalizedTargets(values: number[]): number[] {
  const total = values.reduce((sum, value) => sum + value, 0);
  if (!Number.isFinite(total) || total <= 0) throw new Error("targets_invalid");
  return values.map((value) => value / total);
}

/**
 * Escalado iterativo proporcional de la matriz a las marginales publicadas.
 *
 * La matriz reconstruida no reproduce por si sola lo que el usuario ve: el
 * 1X2 pasa por calibracion de temperatura (DEC-199) y Ambos marcan viene de
 * un modelo propio (Fase 106), no de la matriz. Sin este paso, elegir un solo
 * mercado devolveria un porcentaje distinto del que la aplicacion acaba de
 * mostrar dos pantallas antes.
 *
 * Cada restriccion es una particion de la rejilla, asi que imponerla es un
 * unico escalado por clase; iterar entre las tres las satisface a la vez. El
 * ajuste mueve la masa dentro de cada clase de forma proporcional, de modo que
 * conserva la estructura de dependencia de la matriz original.
 */
export function rakeToMargins(
  matrix: number[][], context: GoalContext,
): number[][] {
  const constraints: Constraint[] = [
    {
      classOf: (home, away) => (home > away ? 0 : home === away ? 1 : 2),
      targets: normalizedTargets([
        context.probabilityHome, context.probabilityDraw, context.probabilityAway,
      ]),
    },
    {
      classOf: (home, away) => (home + away > 2 ? 0 : 1),
      targets: normalizedTargets([
        context.probabilityOver25, 1 - context.probabilityOver25,
      ]),
    },
    {
      classOf: (home, away) => (home > 0 && away > 0 ? 0 : 1),
      targets: normalizedTargets([
        context.probabilityBtts, 1 - context.probabilityBtts,
      ]),
    },
  ];
  let grid = matrix.map((row) => [...row]);
  for (let iteration = 0; iteration < RAKING_ITERATIONS; iteration += 1) {
    let drift = 0;
    for (const constraint of constraints) {
      const mass = constraint.targets.map(() => 0);
      grid.forEach((row, home) => row.forEach((cell, away) => {
        mass[constraint.classOf(home, away)] += cell;
      }));
      const factors = constraint.targets.map((target, index) => (
        mass[index] > 0 ? target / mass[index] : 0
      ));
      constraint.targets.forEach((target, index) => {
        drift = Math.max(drift, Math.abs(mass[index] - target));
      });
      grid = grid.map((row, home) => row.map((cell, away) => (
        cell * factors[constraint.classOf(home, away)]
      )));
    }
    if (drift < RAKING_TOLERANCE) break;
  }
  return grid;
}

const GOAL_PREDICATES: Record<GoalMarketKey, (home: number, away: number) => boolean> = {
  home: (home, away) => home > away,
  draw: (home, away) => home === away,
  away: (home, away) => home < away,
  over_2_5: (home, away) => home + away > 2,
  under_2_5: (home, away) => home + away <= 2,
  btts_yes: (home, away) => home > 0 && away > 0,
  btts_no: (home, away) => home === 0 || away === 0,
};

/**
 * Probabilidad de que ocurran a la vez todos los mercados de gol elegidos.
 *
 * Suma la masa de las celdas que cumplen todas las condiciones. Con una sola
 * seleccion devuelve exactamente la probabilidad publicada de ese mercado; dos
 * selecciones incompatibles -"gana el local" y "gana el visitante", o Mas y
 * Menos de 2.5- dan cero sin necesitar ninguna regla especial.
 */
export function goalJoint(
  context: GoalContext, markets: GoalMarketKey[], maxGoals: number = MAX_GOALS,
): number {
  if (!markets.length) return 1;
  const grid = rakeToMargins(scoreMatrix(context, maxGoals), context);
  let total = 0;
  grid.forEach((row, home) => row.forEach((cell, away) => {
    if (markets.every((market) => GOAL_PREDICATES[market](home, away))) total += cell;
  }));
  return clampProbability(total);
}

/* ------------------------------------------------------------------ *
 * Lineas sobre la misma variable                                      *
 * ------------------------------------------------------------------ */

function clampProbability(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

/**
 * Probabilidad de que se cumplan a la vez varias lineas de la misma variable.
 *
 * Las lineas son semienteras, asi que "mas de a" y "menos de b" delimitan el
 * intervalo `a < X < b` y la interseccion es exacta:
 *
 *   - solo overs  -> P(X > max a)
 *   - solo unders -> P(X < min b) = 1 - P(X > min b)
 *   - mezcla      -> P(X > a) - P(X > b), y cero si a >= b
 *
 * Multiplicar aqui seria un error de bulto: "mas de 4.5 corners" y "mas de
 * 6.5 corners" no son dos eventos independientes, son el mismo evento.
 */
export function countGroupJoint(picks: CountPick[]): { probability: number; note: string } {
  if (!picks.length) return { probability: 1, note: "" };
  const overs = picks.filter((pick) => pick.direction === "over");
  const unders = picks.filter((pick) => pick.direction === "under");
  const lowest = overs.length
    ? overs.reduce((best, pick) => (pick.line > best.line ? pick : best))
    : null;
  const highest = unders.length
    ? unders.reduce((best, pick) => (pick.line < best.line ? pick : best))
    : null;
  if (lowest && highest) {
    if (lowest.line >= highest.line) {
      return { probability: 0, note: "Las líneas elegidas se excluyen entre sí." };
    }
    const value = lowest.overProbability - highest.overProbability;
    if (value < 0) {
      // Dos escaleras distintas (auditada y rejilla) pueden publicar la misma
      // variable con probabilidades que no son monotonas entre si. Preferimos
      // declararlo a servir una resta negativa disfrazada de probabilidad.
      return {
        probability: 0,
        note: "Las dos escaleras no coinciden en esta variable; no se publica un conjunto.",
      };
    }
    return {
      probability: clampProbability(value),
      note: picks.length > 1 ? "Es una sola variable: se recorta el intervalo, no se multiplica." : "",
    };
  }
  if (lowest) {
    return {
      probability: clampProbability(lowest.overProbability),
      note: overs.length > 1 ? "Es una sola variable: manda la línea más alta, no se multiplica." : "",
    };
  }
  return {
    probability: clampProbability(1 - (highest as CountPick).overProbability),
    note: unders.length > 1 ? "Es una sola variable: manda la línea más baja, no se multiplica." : "",
  };
}

/* ------------------------------------------------------------------ *
 * Conjunta completa                                                   *
 * ------------------------------------------------------------------ */

const GOAL_BLOCK_KEY = "__goals__";

function groupBy<T>(items: T[], key: (item: T) => string): Map<string, T[]> {
  const groups = new Map<string, T[]>();
  for (const item of items) {
    const bucket = groups.get(key(item));
    if (bucket) bucket.push(item);
    else groups.set(key(item), [item]);
  }
  return groups;
}

function goalContextOf(picks: GoalPick[]): GoalContext {
  // Todas las selecciones de gol del mismo partido comparten contexto; se
  // toma la mas reciente porque es la que se congelo con la lectura mas nueva
  // de la prediccion.
  return picks.reduce((latest, pick) => (pick.addedAt > latest.addedAt ? pick : latest)).goals;
}

function matchLabel(match: MatchRef): string {
  return `${match.homeName} vs ${match.awayName}`;
}

/**
 * Combina todas las selecciones en una unica probabilidad conjunta.
 *
 * Devuelve tambien el desglose por partido y por bloque, y el producto ingenuo
 * de todas las marginales, para que la interfaz pueda mostrar en que se
 * diferencian y por que.
 */
export function computeJoint(picks: Pick[]): JointResult {
  const warnings: string[] = [];
  const matches: MatchBreakdown[] = [];
  let probability = 1;
  let independentProduct = 1;
  for (const pick of picks) independentProduct *= clampProbability(pick.probability);

  for (const [, group] of groupBy(picks, (pick) => pick.match.matchId)) {
    const match = group[0].match;
    const blocks: BlockBreakdown[] = [];
    let degraded = "";
    let matchProbability = 1;

    const goalPicks = group.filter((pick): pick is GoalPick => pick.kind === "goal");
    if (goalPicks.length) {
      const markets = goalPicks.map((pick) => pick.market);
      let value: number;
      let exact = true;
      let note = goalPicks.length > 1
        ? "Conjunta exacta sobre la matriz de marcadores del partido."
        : "";
      try {
        value = goalJoint(goalContextOf(goalPicks), markets);
      } catch {
        // Sin lambdas usables no hay matriz que sumar. Se degrada al producto,
        // pero se dice: un numero silenciosamente peor es peor que un aviso.
        value = goalPicks.reduce((acc, pick) => acc * clampProbability(pick.probability), 1);
        exact = false;
        degraded = "goal_matrix_unavailable";
        note = "Sin lambdas utilizables: los mercados de gol se multiplicaron como si fueran independientes.";
        warnings.push(
          `${matchLabel(match)}: no se pudo reconstruir la matriz de marcadores; ` +
          "sus mercados de gol se combinaron con un supuesto de independencia que no se sostiene.",
        );
      }
      blocks.push({
        key: GOAL_BLOCK_KEY, label: "Goles", probability: value, exact, note, picks: goalPicks,
      });
      matchProbability *= value;
    }

    const countPicks = group.filter((pick): pick is CountPick => pick.kind === "count");
    for (const [groupKey, groupPicks] of groupBy(countPicks, (pick) => pick.groupKey)) {
      const { probability: value, note } = countGroupJoint(groupPicks);
      blocks.push({
        key: groupKey, label: groupPicks[0].groupLabel, probability: value,
        exact: true, note, picks: groupPicks,
      });
      matchProbability *= value;
    }

    if (blocks.length > 1) {
      warnings.push(
        `${matchLabel(match)}: se combinaron ${blocks.length} variables distintas del mismo ` +
        "partido suponiéndolas independientes entre sí.",
      );
    }
    matches.push({
      match, probability: clampProbability(matchProbability), blocks, degraded,
    });
    probability *= matchProbability;
  }

  return {
    probability: clampProbability(probability),
    matches,
    independentProduct: clampProbability(independentProduct),
    impossible: probability <= 0 && picks.length > 0,
    warnings,
  };
}

/* ------------------------------------------------------------------ *
 * Utilidades de presentacion                                          *
 * ------------------------------------------------------------------ */

/** Formatea una probabilidad conjunta, que suele ser pequena. */
export function jointPercentage(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value <= 0) return "0%";
  if (value < 0.001) return "<0.1%";
  if (value < 0.1) return `${(value * 100).toFixed(2)}%`;
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * Cuota justa equivalente, sin margen.
 *
 * No es una cuota de mercado ni una recomendacion: es 1/p, la lectura mas
 * directa de "una vez cada cuantas". ROI, valor y Kelly siguen bloqueados.
 */
export function fairOdds(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "—";
  return (1 / value).toFixed(2);
}
