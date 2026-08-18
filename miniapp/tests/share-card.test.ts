import { describe, expect, it } from "vitest";

import { isPublicRoute } from "@/lib/public-routes";
import {
  CARD_BAND_MAX, CARD_BAND_MIN, SHARE_CARD_VERSION, bandCell, buildShareCard,
  clip, headlineOutcome, initials, isShareToken, shareFixtureKey, shareToken,
  teamRows,
} from "@/lib/share-card";

/** Escalera con la forma de `distributional_market_view.ladder`. */
function ladder(lines: Array<[number, number]>) {
  return lines.map(([line, over]) => ({
    line, over_probability: over, under_probability: 1 - over,
  }));
}

function group(
  teamSide: string, metric: string, period: string,
  lines: Array<[number, number]>,
) {
  return {
    key: `${teamSide}_${metric}_${period}`,
    team_side: teamSide, metric, period, ladder: ladder(lines),
  };
}

/** Escalera realista: baja de casi seguro a casi imposible. */
const FULL_LADDER: Array<[number, number]> = [
  [0.5, 0.99], [1.5, 0.92], [2.5, 0.78], [3.5, 0.62],
  [4.5, 0.44], [5.5, 0.27], [6.5, 0.14], [7.5, 0.06],
];

function prediction() {
  return {
    probability_home: 0.52, probability_draw: 0.26, probability_away: 0.22,
    probability_over_2_5: 0.61, probability_btts: 0.58,
    fixture: { home_team_name: "Puebla", away_team_name: "Guadalajara" },
    experimental_team_markets: {
      distributional_market_view: [
        group("home", "corners", "first_half", FULL_LADDER),
        group("home", "corners", "second_half", FULL_LADDER),
        group("home", "corners", "full_match", FULL_LADDER),
        group("home", "shots", "first_half", FULL_LADDER),
        group("away", "yellow_cards", "full_match", FULL_LADDER),
      ],
    },
  };
}

const identity = {
  leagueSlug: "mex.1", homeName: "Puebla", awayName: "Guadalajara",
  kickoffTs: "2026-08-15T02:00:00.000Z",
};

describe("band selection", () => {
  it("publishes the most decisive line that stays inside the band", () => {
    // Se consideran las dos direcciones de cada linea, no solo el `over`. El
    // mejor candidato de esta escalera es el `under` de 5.5 (1 - 0.27 = 0.73):
    // por encima del 0.62 del mejor `over`, y todavia bajo el techo.
    const cell = bandCell(ladder(FULL_LADDER));

    expect(cell?.line).toBe(5.5);
    expect(cell?.direction).toBe("under");
    expect(cell?.probability).toBeCloseTo(0.73, 6);
  });

  it("never publishes a probability outside the band", () => {
    for (const over of [0.99, 0.92, 0.78, 0.62, 0.44, 0.27, 0.14, 0.06]) {
      const cell = bandCell(ladder([[2.5, over]]));
      if (!cell) continue;
      expect(cell.probability).toBeGreaterThanOrEqual(CARD_BAND_MIN);
      expect(cell.probability).toBeLessThanOrEqual(CARD_BAND_MAX);
    }
  });

  it("drops the obvious line instead of publishing it", () => {
    // "Mas de 0.5 corners" al 99% es el ejemplo que motivo la banda: no dice
    // nada del partido. El techo lo descarta sin una lista de casos a mano.
    const cell = bandCell(ladder([[0.5, 0.99], [1.5, 0.97]]));

    expect(cell).toBeNull();
  });

  it("uses the under side when that is the informative one", () => {
    const cell = bandCell(ladder([[2.5, 0.33]]));

    expect(cell?.line).toBe(2.5);
    expect(cell?.direction).toBe("under");
    expect(cell?.probability).toBeCloseTo(0.67, 6);
  });

  it("leaves the cell empty when the whole ladder is a coin flip", () => {
    expect(bandCell(ladder([[1.5, 0.5], [2.5, 0.48]]))).toBeNull();
  });

  it("leaves the cell empty when there is no ladder at all", () => {
    expect(bandCell(undefined)).toBeNull();
    expect(bandCell([])).toBeNull();
  });

  it("breaks ties deterministically so two freezes cannot differ", () => {
    // Misma probabilidad en dos lineas: gana la mas baja, siempre.
    const cell = bandCell(ladder([[4.5, 0.7], [2.5, 0.7]]));

    expect(cell?.line).toBe(2.5);
  });

  it("derives the under side when the payload omits it", () => {
    const cell = bandCell([{ line: 2.5, over_probability: 0.3 }]);

    expect(cell?.direction).toBe("under");
    expect(cell?.probability).toBeCloseTo(0.7, 6);
  });
});

describe("team matrix", () => {
  it("builds one row per metric and one cell per period", () => {
    const rows = teamRows(prediction().experimental_team_markets, "home");

    expect(rows.map((row) => row.metric))
      .toEqual(["corners", "shots", "yellow_cards"]);
    expect(rows.every((row) => row.cells.length === 3)).toBe(true);
  });

  it("keeps a metric with no data as empty cells instead of dropping the row", () => {
    // La tabla es una rejilla fija: quitar la fila desalinearia las columnas.
    const rows = teamRows(prediction().experimental_team_markets, "home");
    const cards = rows.find((row) => row.metric === "yellow_cards");

    expect(cards?.cells).toEqual([null, null, null]);
  });

  it("reads each side separately", () => {
    const markets = prediction().experimental_team_markets;
    const home = teamRows(markets, "home");
    const away = teamRows(markets, "away");

    expect(home.find((row) => row.metric === "shots")?.cells[0]).not.toBeNull();
    expect(away.find((row) => row.metric === "shots")?.cells[0]).toBeNull();
    expect(away.find((row) => row.metric === "yellow_cards")?.cells[2])
      .not.toBeNull();
  });

  it("degrades to empty cells when the view is missing", () => {
    const rows = teamRows(null, "home");

    expect(rows).toHaveLength(3);
    expect(rows.every((row) => row.cells.every((cell) => cell === null))).toBe(true);
  });
});

describe("share card", () => {
  it("stamps the current format version", () => {
    expect(buildShareCard(prediction(), identity).version)
      .toBe(SHARE_CARD_VERSION);
  });

  it("names only the most likely 1X2 outcome", () => {
    const card = buildShareCard(prediction(), identity);

    expect(card.outcomeLabel).toBe("Puebla");
    expect(card.outcomeProbability).toBe(0.52);
  });

  it("calls a draw the headline when it is the highest", () => {
    expect(headlineOutcome("Puebla", "Guadalajara", {
      probability_home: 0.3, probability_draw: 0.4, probability_away: 0.3,
    })).toEqual({ label: "Empate", probability: 0.4 });
  });

  it("states the likely side of BTTS rather than always the yes", () => {
    const likely = buildShareCard(prediction(), identity);
    expect(likely.bttsLabel).toBe("Sí");
    expect(likely.bttsProbability).toBeCloseTo(0.58, 6);

    const unlikely = buildShareCard({ probability_btts: 0.38 }, identity);
    expect(unlikely.bttsLabel).toBe("No");
    expect(unlikely.bttsProbability).toBeCloseTo(0.62, 6);
  });

  it("carries the frozen logos through to both teams", () => {
    const card = buildShareCard(prediction(), {
      ...identity, homeLogo: "data:image/png;base64,AAA", awayLogo: "",
    });

    expect(card.home.logo).toBe("data:image/png;base64,AAA");
    expect(card.away.logo).toBe("");
  });

  it("clamps a malformed probability instead of publishing it", () => {
    const card = buildShareCard(
      { probability_home: 1.4, probability_draw: "x", probability_away: -1 },
      identity);

    expect(card.outcomeProbability).toBe(1);
    expect(card.outcomeLabel).toBe("Puebla");
  });

  it("falls back to the caller identity when the payload has no fixture", () => {
    const card = buildShareCard({}, identity);

    expect(card.home.name).toBe("Puebla");
    expect(card.away.name).toBe("Guadalajara");
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
    // Satori no recorta lo que se desborda: pinta encima.
    const clipped = clip("Wolverhampton Wanderers", 16);

    expect(clipped).toHaveLength(16);
    expect(clipped.endsWith("…")).toBe(true);
  });

  it("does not leave a dangling space before the ellipsis", () => {
    expect(clip("Deportivo La Coruna", 14)).toBe("Deportivo La…");
  });

  it("falls back to initials when there is no crest to paint", () => {
    expect(initials("Club Puebla")).toBe("CP");
    expect(initials("Guadalajara")).toBe("G");
    expect(initials("   ")).toBe("?");
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
