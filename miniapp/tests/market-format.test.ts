import { describe, expect, it } from "vitest";

import { countLabel, edgeLabel, probabilityWidth } from "@/lib/client-api";

describe("adaptive market formatting helpers", () => {
  it("clamps the probability bar inside the visible track", () => {
    expect(probabilityWidth(0.42)).toBe("42%");
    expect(probabilityWidth(0)).toBe("0%");
    expect(probabilityWidth(1)).toBe("100%");
    expect(probabilityWidth(1.4)).toBe("100%");
    expect(probabilityWidth(-0.3)).toBe("0%");
    expect(probabilityWidth("no es un número")).toBe("0%");
  });

  it("signs the edge against baseline in percentage points", () => {
    expect(edgeLabel(0.71, 0.66)).toBe("+5.0 pp");
    expect(edgeLabel(0.41, 0.46)).toBe("−5.0 pp");
    expect(edgeLabel(0.5, 0.5)).toBe("+0.0 pp");
    expect(edgeLabel(undefined, 0.5)).toBe("—");
  });

  it("renders expected counts with a single decimal", () => {
    expect(countLabel(3.4321)).toBe("3.4");
    expect(countLabel(0)).toBe("0.0");
    expect(countLabel(null)).toBe("0.0");
    expect(countLabel("sin dato")).toBe("—");
  });
});
