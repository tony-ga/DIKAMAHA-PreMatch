import { describe, expect, it } from "vitest";

import { orderByPeriod, periodLabel } from "@/lib/market-grid";

describe("periodLabel", () => {
  it("traduce los tres periodos conocidos", () => {
    expect(periodLabel("first_half")).toBe("Primer tiempo");
    expect(periodLabel("second_half")).toBe("Segundo tiempo");
    expect(periodLabel("full_match")).toBe("Partido completo");
  });

  it("cae a un formato legible ante un periodo desconocido", () => {
    expect(periodLabel("extra_time")).toBe("extra time");
  });
});

describe("orderByPeriod", () => {
  it("ordena primer tiempo, segundo tiempo y partido completo en ese orden", () => {
    const rows = [
      { key: "b", period: "full_match" },
      { key: "a", period: "second_half" },
      { key: "c", period: "first_half" },
    ];

    const ordered = orderByPeriod(rows).map((row) => row.period);

    expect(ordered).toEqual(["first_half", "second_half", "full_match"]);
  });

  it("desempata alfabéticamente por clave dentro del mismo periodo", () => {
    const rows = [
      { key: "home_shots_first_half", period: "first_half" },
      { key: "away_corners_first_half", period: "first_half" },
    ];

    const ordered = orderByPeriod(rows).map((row) => row.key);

    expect(ordered).toEqual(["away_corners_first_half", "home_shots_first_half"]);
  });

  it("no muta el arreglo original", () => {
    const rows = [{ key: "b", period: "full_match" }, { key: "a", period: "first_half" }];
    const original = [...rows];

    orderByPeriod(rows);

    expect(rows).toEqual(original);
  });
});
