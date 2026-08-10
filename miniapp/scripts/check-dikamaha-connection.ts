type Check = { name: string; path: string; validate(payload: Record<string, unknown>): boolean };

export {};

const providerDate = new Date().toISOString().slice(0, 10).replaceAll("-", "");

const checks: Check[] = [
  { name: "readiness", path: "/v1/readiness", validate: (payload) => payload.ready === true },
  { name: "models", path: "/v1/models", validate: (payload) => Array.isArray(payload.models) },
  { name: "leagues", path: "/v1/explorer/leagues", validate: (payload) => Array.isArray(payload.leagues) },
  { name: "dates", path: "/v1/explorer/dates?mode=past&days=1", validate: (payload) => Array.isArray(payload.dates) },
  { name: "upcoming", path: "/v1/upcoming?limit=1", validate: (payload) => Array.isArray(payload.fixtures) },
  { name: "markets", path: `/v1/provider/markets?league=col.1&date=${providerDate}`, validate: (payload) => Array.isArray(payload.fixtures) },
];

const baseUrl = process.env.DIKAMAHA_BOT_API_URL?.replace(/\/$/, "");
if (!baseUrl) throw new Error("DIKAMAHA_BOT_API_URL_missing");
const apiKey = process.env.DIKAMAHA_API_KEY;
const results: Array<{ name: string; status: number; valid: boolean }> = [];

for (const check of checks) {
  const response = await fetch(`${baseUrl}${check.path}`, {
    cache: "no-store",
    headers: {
      "X-Request-ID": `miniapp-connectivity-${crypto.randomUUID()}`,
      ...(apiKey ? { "X-Dikamaha-Key": apiKey } : {}),
    },
    signal: AbortSignal.timeout(45_000),
  });
  const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
  results.push({ name: check.name, status: response.status, valid: response.ok && check.validate(payload) });
}

const failed = results.filter((result) => !result.valid);
process.stdout.write(`${JSON.stringify({ status: failed.length ? "failed" : "connected", checks: results })}\n`);
if (failed.length) process.exitCode = 1;
