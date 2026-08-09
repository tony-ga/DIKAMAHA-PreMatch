import { describe, expect, it } from "vitest";

import { favoriteSchema, subscriptionPatchSchema, subscriptionSchema } from "@/lib/validation";

describe("Mini App mutation contracts", () => {
  it("accepts a bounded shadow-market rule", () => {
    expect(subscriptionSchema.parse({
      ruleType: "market_threshold",
      leagueSlug: "eng.1",
      marketKey: "probability_home",
      comparator: "gte",
      threshold: 0.7,
      cooldownSeconds: 300,
    }).cooldownSeconds).toBe(300);
  });

  it("rejects incomplete rules and too-short cooldowns", () => {
    expect(subscriptionSchema.safeParse({ ruleType: "market_threshold", leagueSlug: "eng.1" }).success).toBe(false);
    expect(subscriptionSchema.safeParse({ ruleType: "kickoff", fixtureId: "1", cooldownSeconds: 299 }).success).toBe(false);
    expect(subscriptionPatchSchema.safeParse({}).success).toBe(false);
  });

  it("limits favorite labels and entity types", () => {
    expect(favoriteSchema.safeParse({ entityType: "fixture", entityId: "1", label: "A vs B" }).success).toBe(true);
    expect(favoriteSchema.safeParse({ entityType: "bet", entityId: "1", label: "A" }).success).toBe(false);
  });
});
