import { describe, expect, it } from "vitest";

import { isPublicRoute } from "@/lib/public-routes";
import {
  buildShareCard, clip, headlineOutcome, isShareToken, shareCardPeriods,
  shareFixtureKey, shareToken,
} from "@/lib/share-card";

/** Grupo con la forma de `distributional_market_view`: PMF y media. */
function countGroup(
  teamSide: string, metric: string, period: string,
  mass: Array<[number, number]>,
) {
  return {
    key: `${teamSide}_${metric}_${period}`,
    team_side: teamSide, metric, period,
    expected_count: mass.reduce((sum, [count, p]) => sum + count * p, 0),
    probability_mass: mass.map(([count, probability]) => ({ count, probability })),
  };
}

/** PMF plana de 0 a 9: los cuantiles 20% y 80% caen en 2 y 8. */
const FLAT_MASS: Array<[number, number]> = Array.from(
  { length: 10 }, (_, count) => [count, 0.1]);

function prediction() {
  return {
    probability_home: 0.52, probability_draw: 0.26, probability_away: 0.22,
    probability_over_2_5: 0.61, probability_btts: 0.48,
    fixture: { home_team_name: "Puebla", away_team_name: "Guadalajara" },
    experimental_team_markets: {
      distributional_market_view: [
        countGroup("total", "corners", "full_match", FLAT_MASS),
        countGroup("total", "shots", "full_match", FLAT_MASS),
        countGroup("total", "yellow_cards", "first_half", FLAT_MASS),
        countGroup("home", "corners", "second_half", FLAT_MASS),
      ],
    },
  };
}

const identity = {
  leagueSlug: "mex.1", homeName: "Puebla", awayName: "Guadalajara",
  kickoffTs: "2026-08-15T02:00:00.000Z",
};

describe("share card", () => {
  it("takes the probabilities the model returned without recomputing them", () => {
    const card = buildShareCard(prediction(), identity);

    expect(card.probabilityHome).toBe(0.52);
    expect(card.probabilityOver25).toBe(0.61);
    expect(card.probabilityBtts).toBe(0.48);
  });

  it("names the most likely 1X2 outcome as the headline", () => {
    const card = buildShareCard(prediction(), identity);

    expect(card.headlineLabel).toBe("Puebla");
    expect(card.headlineProbability).toBe(0.52);
  });

  it("calls a draw the headline when it is the highest", () => {
    expect(headlineOutcome({
      homeName: "Puebla", awayName: "Guadalajara",
      probabilityHome: 0.3, probabilityDraw: 0.4, probabilityAway: 0.3,
    })).toEqual({ label: "Empate", probability: 0.4 });
  });

  it("publishes only the combined-total side", () => {
    // El lado `home` del ejemplo no debe aparecer: la tarjeta sólo promete
    // totales, y mostrar un lado distinto bajo la misma etiqueta diria algo
    // que no es.
    const periods = shareCardPeriods(prediction().experimental_team_markets);
    const metrics = periods.flatMap((period) => period.lines.map((line) => line.metric));

    expect(periods.map((period) => period.period))
      .toEqual(["first_half", "full_match"]);
    expect(metrics).toEqual(["yellow_cards", "corners", "shots"]);
  });

  it("summarises a group as its mean and 20-80 central interval", () => {
    // Cuartos: exactos en binario, así el acumulado no depende del orden de
    // suma. `_quantile` devuelve el menor conteo cuya CDF alcanza el objetivo,
    // así que 20% cae en 0 (CDF 0.25) y 80% en 3 (CDF 0.75 < 0.8, luego 1.0).
    const [period] = shareCardPeriods({
      distributional_market_view: [
        countGroup("total", "corners", "full_match",
          [[0, 0.25], [1, 0.25], [2, 0.25], [3, 0.25]]),
      ],
    });

    expect(period.lines[0].expected).toBe(1.5);
    expect(period.lines[0].intervalLow).toBe(0);
    expect(period.lines[0].intervalHigh).toBe(3);
  });

  it("distinguishes the periods instead of repeating one line", () => {
    // El defecto que motivó el cambio: con una linea over/under unica la
    // rejilla acotada publicaba "Tiros - Mas de 8.5" en los tres periodos,
    // porque su tope de 9.5 queda por debajo del rango util de los tiros. La
    // media no puede colapsar asi: es distinta por construccion.
    const periods = shareCardPeriods({
      distributional_market_view: [
        countGroup("total", "shots", "first_half",
          [[10, 0.5], [12, 0.5]]),
        countGroup("total", "shots", "second_half",
          [[12, 0.5], [14, 0.5]]),
        countGroup("total", "shots", "full_match",
          [[22, 0.5], [26, 0.5]]),
      ],
    });
    const means = periods.flatMap((period) => period.lines.map((line) => line.expected));

    expect(means).toEqual([11, 13, 24]);
    expect(new Set(means).size).toBe(3);
  });

  it("omits a group whose distribution is missing instead of inventing one", () => {
    expect(shareCardPeriods({ distributional_market_view: [] })).toEqual([]);
    expect(shareCardPeriods({
      distributional_market_view: [
        { team_side: "total", metric: "corners", period: "full_match" },
      ],
    })).toEqual([]);
    expect(shareCardPeriods(null)).toEqual([]);
  });

  it("clamps a malformed probability instead of publishing it", () => {
    const card = buildShareCard(
      { probability_home: 1.4, probability_draw: "x", probability_away: -1 },
      identity);

    expect(card.probabilityHome).toBe(1);
    expect(card.probabilityDraw).toBe(0);
    expect(card.probabilityAway).toBe(0);
  });

  it("falls back to the caller identity when the payload has no fixture", () => {
    const card = buildShareCard({}, identity);

    expect(card.homeName).toBe("Puebla");
    expect(card.awayName).toBe("Guadalajara");
  });
});

describe("share token", () => {
  it("issues an unguessable token that the reader accepts", () => {
    const token = shareToken();

    expect(isShareToken(token)).toBe(true);
    expect(token).toHaveLength(43);
    expect(shareToken()).not.toBe(token);
  });

  it("rejects anything that is not a token, so the URL never reaches the query", () => {
    expect(isShareToken("")).toBe(false);
    expect(isShareToken("../../etc/passwd")).toBe(false);
    expect(isShareToken("a".repeat(42))).toBe(false);
    expect(isShareToken(`${"a".repeat(43)}=`)).toBe(false);
  });

  it("builds the same fixture key the channel publisher uses", () => {
    expect(shareFixtureKey("mex.1", 10)).toBe("mex.1:10");
  });
});

describe("card layout guards", () => {
  it("keeps a short name untouched", () => {
    expect(clip("Club Puebla", 24)).toBe("Club Puebla");
  });

  it("bounds a long name so the image height stays fixed", () => {
    // Satori no recorta lo que se desborda: pinta encima. Un nombre largo sin
    // acotar empujaba el pie sobre la ultima fila de mercados.
    const clipped = clip("Wolverhampton Wanderers", 16);

    expect(clipped).toHaveLength(16);
    expect(clipped.endsWith("…")).toBe(true);
  });

  it("does not leave a dangling space before the ellipsis", () => {
    // El corte cae justo en un espacio: sin `trimEnd` quedaría "La …".
    expect(clip("Deportivo La Coruna", 14)).toBe("Deportivo La…");
  });
});

describe("public routes", () => {
  it("treats only the shared-card routes as public", () => {
    expect(isPublicRoute("/s/abc")).toBe(true);
    expect(isPublicRoute("/s/abc/image")).toBe(true);
    expect(isPublicRoute("/")).toBe(false);
    expect(isPublicRoute("/historial")).toBe(false);
    // No debe bastar con que la ruta empiece por "s".
    expect(isPublicRoute("/settings")).toBe(false);
  });
});
