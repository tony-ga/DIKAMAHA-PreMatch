import { describe, expect, it } from "vitest";

import { queryString } from "@/lib/client-api";
import { explorerCapabilities, resolveExplorerPath } from "@/lib/explorer";

describe("bot parity explorer contract", () => {
  it("allows every DIKAMAHA explorer capability used by Telegram", () => {
    expect(explorerCapabilities).toEqual(expect.arrayContaining([
      "leagues", "dates", "fixtures", "fixture/context", "match/plays",
      "match/statistics", "teams", "team/roster", "player",
    ]));
    expect(resolveExplorerPath(["match", "plays"])).toBe("/v1/explorer/match/plays");
    expect(resolveExplorerPath(["team", "roster"])).toBe("/v1/explorer/team/roster");
  });

  it("rejects arbitrary or ESPN-like proxy paths", () => {
    expect(resolveExplorerPath(["https:", "site.api.espn.com"])).toBeNull();
    expect(resolveExplorerPath(["metrics"])).toBeNull();
    expect(resolveExplorerPath(["..", "models"])).toBeNull();
  });

  it("encodes filters without emitting empty values", () => {
    expect(queryString({ league: "eng.1", date: "20260808", query: "" }))
      .toBe("?league=eng.1&date=20260808");
  });
});
