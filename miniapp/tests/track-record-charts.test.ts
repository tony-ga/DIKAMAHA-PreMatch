import { describe, expect, it } from "vitest";

import {
  dailyHitRateSeries,
  leagueHitRateSeries,
  officialMarketRateSeries,
  shadowMarketRateSeries,
} from "@/lib/track-record-charts";

function match(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    league_slug: "esp.1",
    kickoff_ts: "2026-08-10T20:00:00Z",
    official_verdicts: {
      one_x_two: { predicted: "home", actual: "home", hit: true },
    },
    ...overrides,
  };
}

describe("officialMarketRateSeries", () => {
  it("omite mercados sin muestra suficiente", () => {
    const points = officialMarketRateSeries({
      one_x_two: { sufficient_sample: true, rate: 0.6, baseline_rate: 0.5, hits: 24, total: 40 },
      over_2_5: { sufficient_sample: false, rate: 0.8, baseline_rate: 0.5, hits: 4, total: 5 },
    });

    expect(points).toEqual([{
      key: "one_x_two", label: "Resultado (1X2)",
      rate: 0.6, baseline: 0.5, hits: 24, total: 40, sufficientSample: true,
    }]);
  });

  it("no revienta con un bloque vacío", () => {
    expect(officialMarketRateSeries(null)).toEqual([]);
    expect(officialMarketRateSeries({})).toEqual([]);
  });
});

describe("dailyHitRateSeries", () => {
  it("agrega los tres mercados oficiales por día en huso de México", () => {
    // 02:00 UTC del 11 sigue siendo 10 de agosto en México (UTC-6): un
    // partido de madrugada no puede aparecer en el día siguiente.
    const points = dailyHitRateSeries([
      match({ kickoff_ts: "2026-08-10T20:00:00Z", official_verdicts: {
        one_x_two: { hit: true }, over_2_5: { hit: false },
      } }),
      match({ kickoff_ts: "2026-08-11T02:00:00Z", official_verdicts: {
        one_x_two: { hit: true },
      } }),
      match({ kickoff_ts: "2026-08-12T20:00:00Z", official_verdicts: {
        one_x_two: { hit: false },
      } }),
    ]);

    expect(points).toHaveLength(2);
    expect(points[0]).toMatchObject({ date: "2026-08-10", hits: 2, total: 3 });
    expect(points[1]).toMatchObject({ date: "2026-08-12", hits: 0, total: 1 });
    // Cronológico: el primer punto es el día más antiguo.
    expect(points[0].date < points[1].date).toBe(true);
  });

  it("ignora partidos sin kickoff_ts parseable", () => {
    expect(dailyHitRateSeries([match({ kickoff_ts: "no-es-una-fecha" })])).toEqual([]);
    expect(dailyHitRateSeries(null)).toEqual([]);
  });
});

describe("leagueHitRateSeries", () => {
  it("ordena por volumen de partidos, no por tasa", () => {
    const points = leagueHitRateSeries([
      // Una liga con 1 acierto de 1 partido no debe ganarle a una con más
      // historial sólo por tener 100%.
      match({ league_slug: "small.1", official_verdicts: { one_x_two: { hit: true } } }),
      match({ league_slug: "big.1", official_verdicts: { one_x_two: { hit: true } } }),
      match({ league_slug: "big.1", official_verdicts: { one_x_two: { hit: false } } }),
      match({ league_slug: "big.1", official_verdicts: { one_x_two: { hit: true } } }),
    ]);

    expect(points[0].league).toBe("big.1");
    expect(points[0]).toMatchObject({ hits: 2, total: 3 });
    expect(points[1].league).toBe("small.1");
  });

  it("respeta el límite de ligas", () => {
    const matches = Array.from({ length: 10 }, (_, index) =>
      match({ league_slug: `league.${index}` }));
    expect(leagueHitRateSeries(matches, 3)).toHaveLength(3);
  });
});

describe("shadowMarketRateSeries", () => {
  it("traduce la clave a una etiqueta sin nombres de equipo", () => {
    const points = shadowMarketRateSeries({
      home_corners_first_half_over_4_5: { hits: 6, total: 9 },
    });

    expect(points).toEqual([{
      key: "home_corners_first_half_over_4_5",
      label: "Local · Córners",
      hits: 6, total: 9, rate: 6 / 9,
    }]);
  });

  it("descarta líneas sin partidos evaluados y ordena por volumen", () => {
    const points = shadowMarketRateSeries({
      vacia: { hits: 0, total: 0 },
      away_shots_second_half_over_10_5: { hits: 2, total: 3 },
      total_yellow_cards_full_match_over_3_5: { hits: 5, total: 8 },
    });

    expect(points).toHaveLength(2);
    expect(points[0].total).toBeGreaterThanOrEqual(points[1].total);
  });
});
