import { describe, expect, it } from "vitest";

import { SHADOW_PREVIEW_SIZE, shadowMarketLabel, shadowMatchEntries, shadowSummary } from "@/lib/track-record";

describe("shadow market key parsing", () => {
  it("decomposes side, metric and period for a first-half line", () => {
    expect(shadowMarketLabel("home_corners_first_half_over_4_5", "Puebla", "Guadalajara"))
      .toBe("Puebla · Córners · 1ª mitad");
  });

  it("decomposes a multi-word metric for the away side", () => {
    expect(shadowMarketLabel("away_shots_on_target_full_match_over_7_5", "Puebla", "Guadalajara"))
      .toBe("Guadalajara · Tiros a puerta · partido completo");
  });

  it("labels a combined total-side line as both teams", () => {
    expect(shadowMarketLabel("total_corners_full_match_over_9_5", "Puebla", "Guadalajara"))
      .toBe("Puebla + Guadalajara · Córners · partido completo");
  });

  it("falls back to a readable metric for a key without a known label", () => {
    expect(shadowMarketLabel("home_offsides_full_match_over_2_5", "Puebla", "Guadalajara"))
      .toBe("Puebla · offsides · partido completo");
  });

  it("degrades a key that does not match the expected shape instead of throwing", () => {
    expect(shadowMarketLabel("unexpected_key_format", "Puebla", "Guadalajara"))
      .toBe("unexpected key format");
  });
});

describe("shadow verdict normalization", () => {
  it("flattens a match's shadow_verdicts object into keyed rows", () => {
    const rows = shadowMatchEntries({
      home_corners_first_half_over_4_5: { predicted: "Más de 4.5", actual: "6 observados", hit: true },
    });

    expect(rows).toEqual([
      { key: "home_corners_first_half_over_4_5", predicted: "Más de 4.5", actual: "6 observados", hit: true },
    ]);
  });

  it("degrades a malformed shadow_verdicts value to an empty list", () => {
    expect(shadowMatchEntries(null)).toEqual([]);
    expect(shadowMatchEntries("not an object")).toEqual([]);
    expect(shadowMatchEntries(undefined)).toEqual([]);
  });
});

describe("shadow aggregate summary", () => {
  it("sums hits and totals across every shadow market", () => {
    const summary = shadowSummary({
      markets: {
        home_corners_first_half_over_4_5: { hits: 6, total: 9 },
        away_shots_on_target_full_match_over_7_5: { hits: 3, total: 9 },
      },
    });

    expect(summary).toEqual({ hits: 9, total: 18 });
  });

  it("returns zero for an empty or missing shadow block", () => {
    expect(shadowSummary({ markets: {} })).toEqual({ hits: 0, total: 0 });
    expect(shadowSummary(null)).toEqual({ hits: 0, total: 0 });
    expect(shadowSummary(undefined)).toEqual({ hits: 0, total: 0 });
  });
});

describe("preview size", () => {
  it("is a small, positive number of lines shown before an explicit expand", () => {
    expect(SHADOW_PREVIEW_SIZE).toBeGreaterThan(0);
    expect(SHADOW_PREVIEW_SIZE).toBeLessThan(10);
  });
});
