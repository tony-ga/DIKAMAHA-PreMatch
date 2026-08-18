import { beforeEach, describe, expect, it } from "vitest";

import type {
  CountPick, GoalContext, GoalMarketKey, GoalPick, MatchRef, Pick,
} from "@/lib/pick-builder";
import {
  computeJoint, countGroupJoint, fairOdds, goalJoint, jointPercentage,
  rakeToMargins, scoreMatrix,
} from "@/lib/pick-builder";
import {
  addPick, clearPicks, hasPick, MAX_PICKS, removePick, resetForTests, snapshot,
  togglePick,
} from "@/lib/pick-store";
import { countPick, goalContextFrom, goalPick } from "@/lib/pick-sources";

/**
 * El contexto de referencia. Sus cinco probabilidades publicadas NO son las
 * marginales crudas de la matriz -igual que en produccion, donde el 1X2 pasa
 * por calibracion de temperatura y Ambos marcan viene de otro modelo-, asi que
 * cualquier prueba que las reproduzca esta ejercitando el ajuste de
 * marginales, no una coincidencia.
 */
const CONTEXT: GoalContext = {
  lambdaHome: 1.62,
  lambdaAway: 1.14,
  tau: -0.043,
  probabilityHome: 0.47,
  probabilityDraw: 0.26,
  probabilityAway: 0.27,
  probabilityOver25: 0.51,
  probabilityBtts: 0.55,
};

const MATCH: MatchRef = {
  matchId: "401",
  league: "esp.1",
  homeTeamId: "83",
  awayTeamId: "86",
  kickoff: "2026-09-01T19:00:00Z",
  homeName: "Betis",
  awayName: "Sevilla",
};

const OTHER_MATCH: MatchRef = { ...MATCH, matchId: "402", homeName: "Cadiz", awayName: "Elche" };

const GROUP = { key: "home_corners_full_match", metric: "corners", team_side: "home", period: "full_match" };
const OTHER_GROUP = { key: "away_yellow_cards_full_match", metric: "yellow_cards", team_side: "away", period: "full_match" };

function goal(match: MatchRef, market: GoalMarketKey): GoalPick {
  return goalPick(match, CONTEXT, market);
}

function count(
  match: MatchRef, group: typeof GROUP, line: number, over: number,
  direction: "over" | "under" = "over",
): CountPick {
  const pick = countPick(match, group, line, over, direction, "audited");
  if (!pick) throw new Error("pick_invalido");
  return pick;
}

describe("matriz de marcadores", () => {
  it("suma exactamente uno", () => {
    const total = scoreMatrix(CONTEXT).flat().reduce((sum, cell) => sum + cell, 0);
    expect(total).toBeCloseTo(1, 12);
  });

  it("aplica la correccion Dixon-Coles de marcadores bajos", () => {
    const withTau = scoreMatrix(CONTEXT);
    const withoutTau = scoreMatrix({ ...CONTEXT, tau: 0 });
    // tau negativo sube 0-0 y 1-1 y baja 1-0 y 0-1, que es todo el efecto de
    // la correccion: si la matriz saliera igual, `tau` no se estaria usando.
    expect(withTau[0][0]).toBeGreaterThan(withoutTau[0][0]);
    expect(withTau[1][1]).toBeGreaterThan(withoutTau[1][1]);
    expect(withTau[1][0]).toBeLessThan(withoutTau[1][0]);
  });

  it("rechaza lambdas no utilizables en vez de inventar una matriz", () => {
    expect(() => scoreMatrix({ ...CONTEXT, lambdaHome: 0 })).toThrow();
    expect(() => scoreMatrix({ ...CONTEXT, lambdaAway: Number.NaN })).toThrow();
  });
});

describe("ajuste a las marginales publicadas", () => {
  it("reproduce las tres marginales a la vez", () => {
    const grid = rakeToMargins(scoreMatrix(CONTEXT), CONTEXT);
    let home = 0, draw = 0, away = 0, over = 0, btts = 0, total = 0;
    grid.forEach((row, h) => row.forEach((cell, a) => {
      total += cell;
      if (h > a) home += cell; else if (h === a) draw += cell; else away += cell;
      if (h + a > 2) over += cell;
      if (h > 0 && a > 0) btts += cell;
    }));
    expect(total).toBeCloseTo(1, 12);
    expect(home).toBeCloseTo(CONTEXT.probabilityHome, 12);
    expect(draw).toBeCloseTo(CONTEXT.probabilityDraw, 12);
    expect(away).toBeCloseTo(CONTEXT.probabilityAway, 12);
    expect(over).toBeCloseTo(CONTEXT.probabilityOver25, 12);
    expect(btts).toBeCloseTo(CONTEXT.probabilityBtts, 12);
  });
});

describe("mercados de gol de un mismo partido", () => {
  // El criterio de exito del usuario: una seleccion unica tiene que devolver
  // exactamente lo que la aplicacion mostro en esa pantalla.
  it.each<[GoalMarketKey, number]>([
    ["home", 0.47], ["draw", 0.26], ["away", 0.27],
    ["over_2_5", 0.51], ["under_2_5", 0.49],
    ["btts_yes", 0.55], ["btts_no", 0.45],
  ])("una sola seleccion (%s) devuelve la probabilidad publicada", (market, expected) => {
    expect(goalJoint(CONTEXT, [market])).toBeCloseTo(expected, 12);
  });

  it("da cero a dos resultados 1X2 incompatibles", () => {
    expect(goalJoint(CONTEXT, ["home", "away"])).toBe(0);
    expect(goalJoint(CONTEXT, ["home", "draw"])).toBe(0);
  });

  it("da cero a mas y menos de 2.5 goles a la vez", () => {
    expect(goalJoint(CONTEXT, ["over_2_5", "under_2_5"])).toBe(0);
  });

  // Valores de referencia calculados aparte, sumando la masa de la matriz
  // ajustada celda a celda. Si el modulo cambia de forma silenciosa, estos
  // numeros dejan de cuadrar.
  it("resuelve la conjunta sobre la matriz, no multiplicando", () => {
    expect(goalJoint(CONTEXT, ["home", "over_2_5"])).toBeCloseTo(0.288888848284287, 12);
    expect(goalJoint(CONTEXT, ["btts_yes", "over_2_5"])).toBeCloseTo(0.422893306154873, 12);
    expect(goalJoint(CONTEXT, ["draw", "btts_no"])).toBeCloseTo(0.068381737738415, 12);
  });

  it("se separa del producto en la direccion que dicta la dependencia", () => {
    // Ambos marcan y Mas de 2.5 se refuerzan: exigirlos juntos es bastante mas
    // probable que multiplicarlos.
    expect(goalJoint(CONTEXT, ["btts_yes", "over_2_5"]))
      .toBeGreaterThan(CONTEXT.probabilityBtts * CONTEXT.probabilityOver25);
    // Empate y "no marcan ambos" se estorban: casi solo el 0-0 los cumple.
    expect(goalJoint(CONTEXT, ["draw", "btts_no"]))
      .toBeLessThan(CONTEXT.probabilityDraw * (1 - CONTEXT.probabilityBtts));
  });

  it("sin seleccion es el suceso seguro", () => {
    expect(goalJoint(CONTEXT, [])).toBe(1);
  });
});

describe("lineas de la misma variable", () => {
  it("dos 'mas de' se quedan con la linea mas alta", () => {
    const result = countGroupJoint([
      count(MATCH, GROUP, 4.5, 0.62), count(MATCH, GROUP, 6.5, 0.24),
    ]);
    expect(result.probability).toBeCloseTo(0.24, 12);
    // Multiplicarlas daria 0.1488: el error que este bloque existe para evitar.
    expect(result.probability).not.toBeCloseTo(0.62 * 0.24, 6);
  });

  it("dos 'menos de' se quedan con la linea mas baja", () => {
    const result = countGroupJoint([
      count(MATCH, GROUP, 4.5, 0.62, "under"), count(MATCH, GROUP, 6.5, 0.24, "under"),
    ]);
    expect(result.probability).toBeCloseTo(1 - 0.62, 12);
  });

  it("un 'mas de' y un 'menos de' recortan el intervalo", () => {
    const result = countGroupJoint([
      count(MATCH, GROUP, 4.5, 0.62), count(MATCH, GROUP, 6.5, 0.24, "under"),
    ]);
    expect(result.probability).toBeCloseTo(0.62 - 0.24, 12);
  });

  it("da cero cuando el intervalo queda vacio", () => {
    const result = countGroupJoint([
      count(MATCH, GROUP, 6.5, 0.24), count(MATCH, GROUP, 4.5, 0.62, "under"),
    ]);
    expect(result.probability).toBe(0);
  });

  it("no publica un conjunto cuando las dos escaleras se contradicen", () => {
    // Escalera no monotona: la linea alta declara mas probabilidad que la baja.
    const result = countGroupJoint([
      count(MATCH, GROUP, 4.5, 0.20), count(MATCH, GROUP, 6.5, 0.55, "under"),
    ]);
    expect(result.probability).toBe(0);
    expect(result.note).toContain("no coinciden");
  });
});

describe("conjunta completa", () => {
  it("multiplica entre partidos distintos", () => {
    const picks: Pick[] = [goal(MATCH, "home"), goal(OTHER_MATCH, "over_2_5")];
    const result = computeJoint(picks);
    expect(result.matches).toHaveLength(2);
    expect(result.probability).toBeCloseTo(0.47 * 0.51, 12);
  });

  it("multiplica entre variables distintas del mismo partido y lo declara", () => {
    const picks: Pick[] = [
      count(MATCH, GROUP, 4.5, 0.62), count(MATCH, OTHER_GROUP, 1.5, 0.4),
    ];
    const result = computeJoint(picks);
    expect(result.matches).toHaveLength(1);
    expect(result.probability).toBeCloseTo(0.62 * 0.4, 12);
    expect(result.warnings.join(" ")).toContain("independientes");
  });

  it("no multiplica dos lineas de la misma variable aunque vayan juntas", () => {
    const picks: Pick[] = [
      count(MATCH, GROUP, 4.5, 0.62), count(MATCH, GROUP, 6.5, 0.24),
    ];
    expect(computeJoint(picks).probability).toBeCloseTo(0.24, 12);
  });

  it("combina goles y conteos del mismo partido en un solo numero", () => {
    const picks: Pick[] = [
      goal(MATCH, "home"), goal(MATCH, "over_2_5"), count(MATCH, GROUP, 4.5, 0.62),
    ];
    const result = computeJoint(picks);
    expect(result.probability).toBeCloseTo(0.288888848284287 * 0.62, 12);
    expect(result.matches[0].blocks).toHaveLength(2);
  });

  it("marca como imposible una combinacion contradictoria", () => {
    const result = computeJoint([goal(MATCH, "home"), goal(MATCH, "away")]);
    expect(result.probability).toBe(0);
    expect(result.impossible).toBe(true);
  });

  it("publica el producto ingenuo para contraste", () => {
    const result = computeJoint([goal(MATCH, "btts_yes"), goal(MATCH, "over_2_5")]);
    expect(result.independentProduct).toBeCloseTo(0.55 * 0.51, 12);
    expect(result.probability).toBeGreaterThan(result.independentProduct);
  });

  it("degrada con aviso explicito si no hay matriz reconstruible", () => {
    const broken: GoalPick = {
      ...goal(MATCH, "home"), goals: { ...CONTEXT, lambdaHome: 0 },
    };
    const result = computeJoint([broken, { ...goal(MATCH, "over_2_5"), goals: { ...CONTEXT, lambdaHome: 0 } }]);
    expect(result.matches[0].degraded).toBe("goal_matrix_unavailable");
    expect(result.matches[0].blocks[0].exact).toBe(false);
    expect(result.warnings.join(" ")).toContain("no se sostiene");
  });

  it("sin selecciones no afirma nada", () => {
    const result = computeJoint([]);
    expect(result.probability).toBe(1);
    expect(result.impossible).toBe(false);
    expect(result.matches).toHaveLength(0);
  });
});

describe("lectura del payload", () => {
  it("toma lambdas, tau y las cinco probabilidades publicadas", () => {
    const context = goalContextFrom({
      lambda_home: 1.62, lambda_away: 1.14,
      probability_home: 0.47, probability_draw: 0.26, probability_away: 0.27,
      probability_over_2_5: 0.51, probability_btts: 0.55,
      audit: { tau_dc: -0.043 },
    });
    expect(context).toEqual(CONTEXT);
  });

  it("acepta la via de respaldo, que no publica tau", () => {
    const context = goalContextFrom({
      lambda_home: 1.2, lambda_away: 1.0,
      probability_home: 0.4, probability_draw: 0.3, probability_away: 0.3,
      probability_over_2_5: 0.5, probability_btts: 0.5, audit: {},
    });
    expect(context?.tau).toBe(0);
  });

  it("devuelve null sin lambdas utilizables, para no ofrecer el boton", () => {
    expect(goalContextFrom({ probability_home: 0.4 })).toBeNull();
    expect(goalContextFrom({
      lambda_home: 0, lambda_away: 1.1,
      probability_home: 0.4, probability_draw: 0.3, probability_away: 0.3,
      probability_over_2_5: 0.5, probability_btts: 0.5,
    })).toBeNull();
  });

  it("identifica la variable por metrica, lado y periodo", () => {
    const pick = count(MATCH, GROUP, 4.5, 0.62);
    expect(pick.groupKey).toBe("home_corners_full_match");
    expect(pick.groupLabel).toBe("Partido completo · Betis · Córners");
    expect(pick.label).toContain("Más de 4.5");
    expect(pick.probability).toBeCloseTo(0.62, 12);
  });

  it("guarda la escalera en una sola direccion aunque se elija 'menos de'", () => {
    const pick = count(MATCH, GROUP, 4.5, 0.62, "under");
    expect(pick.overProbability).toBeCloseTo(0.62, 12);
    expect(pick.probability).toBeCloseTo(0.38, 12);
  });

  it("rechaza una linea sin numero utilizable", () => {
    expect(countPick(MATCH, GROUP, Number.NaN, 0.5, "over", "grid")).toBeNull();
    expect(countPick(MATCH, GROUP, 4.5, Number.NaN, "over", "grid")).toBeNull();
  });
});

describe("almacen de selecciones", () => {
  beforeEach(() => resetForTests([]));

  it("agrega, quita y alterna sin duplicar", () => {
    const pick = goal(MATCH, "home");
    expect(addPick(pick)).toBe(true);
    expect(addPick(pick)).toBe(false);
    expect(snapshot()).toHaveLength(1);
    expect(hasPick(pick.id)).toBe(true);
    togglePick(pick);
    expect(snapshot()).toHaveLength(0);
  });

  it("da la misma identidad al mismo mercado del mismo partido", () => {
    expect(goal(MATCH, "home").id).toBe(goal(MATCH, "home").id);
    expect(goal(MATCH, "home").id).not.toBe(goal(OTHER_MATCH, "home").id);
    expect(count(MATCH, GROUP, 4.5, 0.62).id)
      .not.toBe(count(MATCH, GROUP, 4.5, 0.62, "under").id);
  });

  it("respeta el tope de selecciones", () => {
    for (let index = 0; index < MAX_PICKS; index += 1) {
      expect(addPick(count(MATCH, GROUP, index + 0.5, 0.5))).toBe(true);
    }
    expect(addPick(goal(MATCH, "home"))).toBe(false);
    expect(snapshot()).toHaveLength(MAX_PICKS);
  });

  it("vacia el constructor de una vez", () => {
    addPick(goal(MATCH, "home"));
    addPick(goal(OTHER_MATCH, "away"));
    clearPicks();
    expect(snapshot()).toHaveLength(0);
  });

  it("ignora quitar algo que no esta", () => {
    addPick(goal(MATCH, "home"));
    removePick("inexistente");
    expect(snapshot()).toHaveLength(1);
  });
});

describe("presentacion", () => {
  it("no redondea a cero una conjunta pequena", () => {
    expect(jointPercentage(0.0004)).toBe("<0.1%");
    expect(jointPercentage(0.0231)).toBe("2.31%");
    expect(jointPercentage(0.4812)).toBe("48.1%");
    expect(jointPercentage(0)).toBe("0%");
  });

  it("expresa la cuota justa como el inverso, sin margen", () => {
    expect(fairOdds(0.25)).toBe("4.00");
    expect(fairOdds(0)).toBe("—");
  });
});
