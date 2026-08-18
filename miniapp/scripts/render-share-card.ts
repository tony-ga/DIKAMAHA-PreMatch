import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import { ImageResponse } from "next/og";

import { SHARE_IMAGE_SIZE, ShareCardImage } from "../lib/share-card-image";
import { buildShareCard } from "../lib/share-card";

/**
 * Escribe un PNG de muestra de la tarjeta compartible.
 *
 * Satori soporta un subconjunto de CSS y sus fallos son visuales -texto que se
 * sale, un bloque que colapsa-, no excepciones: sin renderizar el archivo no
 * hay forma de revisar el diseño. Se ejecuta a mano, no en CI:
 *
 *   npx tsx scripts/render-share-card.ts [salida.png]
 *
 * Las escaleras de muestra NO se escriben a mano: se derivan de una intensidad
 * esperada por equipo, metrica y periodo, con la misma Poisson y los mismos
 * topes de escalera que `LADDER_MAXIMUMS` (`src/team_count_market_runtime.py`).
 * Una muestra inventada mostro una vez la misma linea en las tres mitades, y
 * hubo que rehacerla para descubrir que el producto real tenia el mismo
 * defecto por otra causa. Una muestra que no obedece la regla que ilustra no
 * sirve para revisar nada.
 */

/** Mismos topes que `LADDER_MAXIMUMS`: numero de lineas por metrica y periodo. */
const LADDER_MAXIMUMS: Record<string, { half: number; full_match: number }> = {
  corners: { half: 7, full_match: 13 },
  shots: { half: 15, full_match: 29 },
  yellow_cards: { half: 6, full_match: 11 },
};

/** Escalera Poisson: `line = threshold + 0.5`, igual que `_ladder_line`. */
function poissonLadder(lambda: number, maximum: number) {
  const pmf: number[] = [];
  let term = Math.exp(-lambda);
  for (let count = 0; count <= maximum + 40; count += 1) {
    pmf.push(term);
    term *= lambda / (count + 1);
  }
  const ladder = [];
  for (let threshold = 0; threshold < maximum; threshold += 1) {
    const over = pmf.slice(threshold + 1).reduce((sum, value) => sum + value, 0);
    ladder.push({
      line: threshold + 0.5,
      over_probability: over,
      under_probability: 1 - over,
    });
  }
  return ladder;
}

// Intensidades por equipo de un partido corriente de liga.
const EXPECTED: Record<string, Record<string, Record<string, number>>> = {
  home: {
    corners: { first_half: 2.5, second_half: 2.9, full_match: 5.4 },
    shots: { first_half: 5.8, second_half: 6.5, full_match: 12.3 },
    yellow_cards: { first_half: 0.8, second_half: 1.4, full_match: 2.2 },
  },
  away: {
    corners: { first_half: 2.2, second_half: 2.7, full_match: 4.9 },
    shots: { first_half: 5.2, second_half: 5.9, full_match: 11.1 },
    yellow_cards: { first_half: 0.9, second_half: 1.5, full_match: 2.4 },
  },
};

const distributional = Object.entries(EXPECTED).flatMap(([side, metrics]) =>
  Object.entries(metrics).flatMap(([metric, periods]) =>
    Object.entries(periods).map(([period, lambda]) => ({
      key: `${side}_${metric}_${period}`,
      team_side: side, metric, period,
      expected_count: lambda,
      ladder: poissonLadder(
        lambda,
        LADDER_MAXIMUMS[metric][period === "full_match" ? "full_match" : "half"]),
    }))));

const card = buildShareCard({
  probability_home: 0.34, probability_draw: 0.29, probability_away: 0.37,
  probability_over_2_5: 0.61, probability_btts: 0.58,
  experimental_team_markets: { distributional_market_view: distributional },
}, {
  leagueSlug: "eng.league_cup",
  homeName: "Wolverhampton Wanderers",
  awayName: "Brighton & Hove Albion",
  kickoffTs: "2026-08-15T02:00:00.000Z",
});

const target = resolve(process.cwd(), process.argv[2] ?? "share-card-sample.png");
const response = new ImageResponse(ShareCardImage({ card }), SHARE_IMAGE_SIZE);
await writeFile(target, Buffer.from(await response.arrayBuffer()));
console.info(`share_card_rendered ${target}`);
for (const team of [card.home, card.away]) {
  console.info(`\n${team.name}`);
  for (const row of team.rows) {
    const cells = row.cells.map((cell) => (cell
      ? `${cell.direction === "under" ? "-" : "+"}${cell.line} ${(cell.probability * 100).toFixed(0)}%`
      : "—").padStart(12));
    console.info(`  ${row.label.padEnd(20)}${cells.join("")}`);
  }
}
