const explorerRoutes = new Map([
  ["leagues", "/v1/explorer/leagues"],
  ["dates", "/v1/explorer/dates"],
  ["fixtures", "/v1/explorer/fixtures"],
  ["fixture/context", "/v1/explorer/fixture/context"],
  ["match/plays", "/v1/explorer/match/plays"],
  ["match/statistics", "/v1/explorer/match/statistics"],
  ["teams", "/v1/explorer/teams"],
  ["team/roster", "/v1/explorer/team/roster"],
  ["player", "/v1/explorer/player"],
]);

export function resolveExplorerPath(path: string[]): string | null {
  return explorerRoutes.get(path.join("/")) ?? null;
}

export const explorerCapabilities = Object.freeze([...explorerRoutes.keys()]);
