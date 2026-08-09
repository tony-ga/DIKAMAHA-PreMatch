import { describe, expect, it } from "vitest";

import { shouldTrigger } from "@/worker/alerts";

function subscription(overrides: Record<string, unknown>): Parameters<typeof shouldTrigger>[0] {
  return {
    id: "sub-1",
    user_id: 42,
    rule_type: "score_change",
    fixture_id: "100",
    league_slug: "eng.1",
    market_key: null,
    comparator: null,
    threshold: null,
    cooldown_seconds: 300,
    last_observation: null,
    last_triggered_at: null,
    ...overrides,
  } as Parameters<typeof shouldTrigger>[0];
}

describe("alert transition engine", () => {
  it("does not alert on the first observation", () => {
    expect(shouldTrigger(subscription({}), { present: true, home: 0, away: 0 })).toBe(false);
  });

  it("alerts only when score changes", () => {
    expect(shouldTrigger(
      subscription({ last_observation: { present: true, home: 0, away: 0 } }),
      { present: true, home: 1, away: 0 },
    )).toBe(true);
  });

  it("does not invent a score change when a fixture leaves the live catalog", () => {
    expect(shouldTrigger(
      subscription({ last_observation: { present: true, home: 2, away: 1 } }),
      { present: false, home: null, away: null },
    )).toBe(false);
  });

  it("detects threshold crossings without repeating above the threshold", () => {
    const rule = subscription({
      rule_type: "market_threshold",
      market_key: "probability_home",
      comparator: "gte",
      threshold: "0.70",
      last_observation: { value: 0.69 },
    });
    expect(shouldTrigger(rule, { value: 0.71 })).toBe(true);
    expect(shouldTrigger(subscription({ ...rule, last_observation: { value: 0.72 } }), { value: 0.73 })).toBe(false);
  });
});
