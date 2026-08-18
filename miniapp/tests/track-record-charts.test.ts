import { describe, expect, it } from "vitest";

import {
  dailyHitRateSeries,
  highProbabilityDailySeries,
  highProbabilityMarketSeries,
  leagueHitRateSeries,
  officialMarketRateSeries,
  reliabilitySeries,
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

describe("reliabilitySeries", () => {
  it("convierte una celda con muestra suficiente a un punto declarado/observado", () => {
    const points = reliabilitySeries({
      cells: [{
        market: "1x2", bucket_low: 0.65, bucket_high: 0.75,
        total: 24, sufficient_sample: true,
        declared_rate: 0.7, observed_rate_prospective: 0.625,
        interval_95: [0.42, 0.79],
      }],
    });

    expect(points).toEqual([{
      key: "1x2:0.65-0.75", label: "1X2 65-75%",
      declared: 0.7, observed: 0.625,
      errorLow: 0.625 - 0.42, errorHigh: 0.79 - 0.625,
      total: 24,
    }]);
  });

  it("omite celdas sin muestra suficiente, igual que officialMarketRateSeries", () => {
    const points = reliabilitySeries({
      cells: [{
        market: "over_2_5", bucket_low: 0.6, bucket_high: 0.7,
        total: 5, sufficient_sample: false, declared_rate: 0.65,
        missing_for_rate: 15,
      }],
    });

    expect(points).toEqual([]);
  });

  it("no revienta con un bloque vacío", () => {
    expect(reliabilitySeries(null)).toEqual([]);
    expect(reliabilitySeries({})).toEqual([]);
  });
});

function pick(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    pick_key: "esp.1:1:1x2:match:full_match:na:home",
    market: "1x2", status: "hit", kickoff_ts: "2026-08-10T20:00:00Z",
    ...overrides,
  };
}

describe("highProbabilityMarketSeries", () => {
  it("agrega por mercado, ignora pendientes y ordena por volumen", () => {
    const points = highProbabilityMarketSeries([
      pick({ market: "1x2", status: "hit" }),
      pick({ market: "1x2", status: "miss" }),
      pick({ market: "home_corners", status: "pending" }),
      pick({ market: "over_2_5", status: "hit" }),
    ]);

    expect(points).toEqual([
      { key: "1x2", label: "1x2", hits: 1, total: 2, rate: 0.5 },
      { key: "over_2_5", label: "over 2 5", hits: 1, total: 1, rate: 1 },
    ]);
  });

  it("no revienta con un bloque vacío", () => {
    expect(highProbabilityMarketSeries(null)).toEqual([]);
  });
});

describe("highProbabilityDailySeries", () => {
  it("agrega volumen diario en huso de México, cronológico", () => {
    const points = highProbabilityDailySeries([
      pick({ kickoff_ts: "2026-08-10T20:00:00Z", status: "hit" }),
      pick({ kickoff_ts: "2026-08-10T21:00:00Z", status: "miss" }),
      pick({ kickoff_ts: "2026-08-12T20:00:00Z", status: "hit" }),
      pick({ kickoff_ts: "2026-08-11T02:00:00Z", status: "pending" }),
    ]);

    expect(points).toEqual([
      { date: "2026-08-10", label: expect.any(String), hits: 1, total: 2 },
      { date: "2026-08-12", label: expect.any(String), hits: 1, total: 1 },
    ]);
  });

  it("no revienta con un bloque vacío", () => {
    expect(highProbabilityDailySeries(null)).toEqual([]);
  });
});
