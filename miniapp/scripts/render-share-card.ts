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
 * Las distribuciones de muestra NO se escriben a mano: se derivan de una
 * intensidad esperada por metrica y periodo. Una muestra inventada mostro una
 * vez la misma linea en las tres mitades, y hubo que rehacerla para descubrir
 * que la version real del producto tenia el mismo defecto por otra causa (el
 * tope de 9.5 de la rejilla). Una muestra que no obedece la regla que ilustra
 * no sirve para revisar nada.
 */

/** PMF de Poisson truncada donde la cola deja de aportar masa. */
function poissonMass(lambda: number): Array<{ count: number; probability: number }> {
  const mass = [];
  let term = Math.exp(-lambda);
  for (let count = 0; count <= Math.ceil(lambda * 3) + 12; count += 1) {
    mass.push({ count, probability: term });
    term *= lambda / (count + 1);
  }
  return mass;
}

// Intensidades totales (ambos equipos) de un partido corriente de liga.
const EXPECTED: Record<string, Record<string, number>> = {
  corners: { first_half: 4.7, second_half: 5.6, full_match: 10.3 },
  shots: { first_half: 11.0, second_half: 12.4, full_match: 23.4 },
  yellow_cards: { first_half: 1.6, second_half: 2.7, full_match: 4.3 },
};

const distributional = Object.entries(EXPECTED).flatMap(([metric, periods]) =>
  Object.entries(periods).map(([period, lambda]) => ({
    key: `total_${metric}_${period}`,
    team_side: "total", metric, period,
    expected_count: lambda,
    probability_mass: poissonMass(lambda),
  })));

const card = buildShareCard({
  probability_home: 0.34, probability_draw: 0.29, probability_away: 0.37,
  probability_over_2_5: 0.61, probability_btts: 0.48,
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
console.info(JSON.stringify(card.periods, null, 2));
