import { describe, expect, it } from "vitest";

import {
  auditedLines, auditedRows, countModelEdge, metricLabel, orderByLine,
  previewLines, teamLabel,
} from "@/lib/audited-ladder";

describe("audited ladder team and metric labels", () => {
  it("resolves the subject of a line to a single team or both", () => {
    expect(teamLabel("home", "Cambridge United", "Barnet")).toBe("Cambridge United");
    expect(teamLabel("away", "Cambridge United", "Barnet")).toBe("Barnet");
    expect(teamLabel("total", "Cambridge United", "Barnet")).toBe("Cambridge United + Barnet");
  });

  it("translates known metrics and falls back to a readable slug", () => {
    expect(metricLabel("corners")).toBe("Córners");
    expect(metricLabel("shots_on_target")).toBe("Tiros a puerta");
    expect(metricLabel("unknown_metric")).toBe("unknown metric");
  });
});

describe("audited ladder line ordering", () => {
  const lines = [
    { line: 4.5, over_probability: 0.32 },
    { line: 0.5, over_probability: 0.98 },
    { line: 2.5, over_probability: 0.71 },
  ];

  it("orders a ladder ascending by line without mutating the input", () => {
    const ordered = orderByLine(lines);

    expect(ordered.map((row) => row.line)).toEqual([0.5, 2.5, 4.5]);
    expect(lines[0].line).toBe(4.5);
  });

  it("selects the lines closest to an even split for the compact preview", () => {
    const wide = [
      { line: 0.5, over_probability: 0.99 },
      { line: 1.5, over_probability: 0.95 },
      { line: 2.5, over_probability: 0.71 },
      { line: 3.5, over_probability: 0.52 },
      { line: 4.5, over_probability: 0.31 },
      { line: 5.5, over_probability: 0.12 },
    ];

    const preview = previewLines(wide, 3);

    // Las tres más cercanas a 0.5 son 3.5 (0.52), 2.5 (0.71) y 4.5 (0.31);
    // el resultado se reordena de forma ascendente por línea.
    expect(preview.map((row) => row.line)).toEqual([2.5, 3.5, 4.5]);
  });

  it("returns every line when there are fewer than the requested preview size", () => {
    const short = [{ line: 1.5, over_probability: 0.6 }];

    expect(previewLines(short, 4)).toHaveLength(1);
  });
});

describe("audited ladder reliability counting", () => {
  it("counts only lines whose reliability is model_edge", () => {
    const lines = [
      { reliability: "model_edge" }, { reliability: "base_rate_driven" },
      { reliability: "model_edge" },
    ];

    expect(countModelEdge(lines)).toBe(2);
  });

  it("returns zero when every line is base-rate driven", () => {
    const lines = [{ reliability: "base_rate_driven" }, { reliability: "base_rate_driven" }];

    expect(countModelEdge(lines)).toBe(0);
  });
});

describe("audited ladder payload normalization", () => {
  it("normalizes a well-formed payload into row records", () => {
    const rows = auditedRows([{ key: "home_corners", metric: "corners" }]);

    expect(rows).toEqual([{ key: "home_corners", metric: "corners" }]);
  });

  it("degrades a malformed payload to an empty list instead of throwing", () => {
    expect(auditedRows(null)).toEqual([]);
    expect(auditedRows(undefined)).toEqual([]);
    expect(auditedRows("not an array")).toEqual([]);
    expect(auditedRows({ not: "an array" })).toEqual([]);
  });

  it("normalizes the nested lines of a single row", () => {
    const lines = auditedLines({ lines: [{ line: 4.5 }] });

    expect(lines).toEqual([{ line: 4.5 }]);
  });

  it("degrades a row with malformed lines to an empty list", () => {
    expect(auditedLines({ lines: "not an array" })).toEqual([]);
    expect(auditedLines({})).toEqual([]);
  });
});
