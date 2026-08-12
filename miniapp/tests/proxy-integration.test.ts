import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";

import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

let server: Server;
let baseUrl = "";
const requests: Array<{ path: string; key: string | undefined }> = [];
let failuresRemaining = 0;
let nextContractRejection: string | null = null;

beforeAll(async () => {
  server = createServer((request, response) => {
    requests.push({ path: request.url ?? "", key: request.headers["x-dikamaha-key"] as string | undefined });
    if (nextContractRejection) {
      const message = nextContractRejection;
      nextContractRejection = null;
      response.writeHead(422, { "Content-Type": "application/json" });
      // Misma forma que `_error()` en dikamaha_service.py: `code` es una
      // clasificación gruesa de tres valores para logs, `message` es la
      // razón específica del rechazo.
      response.end(JSON.stringify({ detail: { code: "contract_validation_error", message } }));
      return;
    }
    if (failuresRemaining > 0) {
      failuresRemaining -= 1;
      response.writeHead(503, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "temporarily_unavailable" }));
      return;
    }
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ leagues: [{ slug: "mex.1", name: "Liga MX" }], count: 1 }));
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  baseUrl = `http://127.0.0.1:${address.port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
});

describe("authenticated BFF to DIKAMAHA connection", () => {
  it("forwards an allowlisted explorer request with server-only credentials", async () => {
    vi.resetModules();
    Object.assign(process.env, {
      NODE_ENV: "test",
      TELEGRAM_BOT_TOKEN: "123456789:abcdefghijklmnopqrstuvwxyzABCDE",
      TELEGRAM_ACCESS_MODE: "private",
      TELEGRAM_ALLOWED_USER_IDS: "42",
      DIKAMAHA_BOT_API_URL: baseUrl,
      DIKAMAHA_API_KEY: "server-only-test-key",
      DATABASE_URL: "postgres://test:test@example.test:5432/test",
      MINIAPP_SESSION_SECRET: "0123456789abcdef0123456789abcdef",
      MINIAPP_ENABLED: "true",
      MINIAPP_ALERTS_ENABLED: "false",
    });
    let token = "";
    vi.doMock("next/headers", () => ({ cookies: async () => ({ get: () => ({ value: token }) }) }));
    const { issueSession } = await import("@/lib/auth/session");
    token = issueSession({ userId: 42, firstName: "Marco" }).token;
    const { NextRequest } = await import("next/server");
    const { proxyGet } = await import("@/lib/proxy");
    const response = await proxyGet(new NextRequest("http://mini.local/api/explorer/leagues"), "/v1/explorer/leagues");
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({ count: 1 });
    expect(requests.at(-1)).toEqual({ path: "/v1/explorer/leagues", key: "server-only-test-key" });
  });

  it("retries transient GET failures before exposing an empty selector", async () => {
    const before = requests.length;
    failuresRemaining = 2;
    const { dikamahaRequest } = await import("@/lib/dikamaha");
    await expect(dikamahaRequest("/v1/explorer/leagues")).resolves.toMatchObject({ count: 1 });
    expect(requests.length - before).toBe(3);
  });

  // Existe por una incidencia real: un pico de contención tras un despliegue
  // hizo que /v1/predict/upcoming devolviera 504, y como sólo los GET se
  // reintentaban, el usuario veía "sin predicción" ante lo que era una falla
  // transitoria de un único intento.
  it("retries a POST marked idempotent, like a prediction compute", async () => {
    const before = requests.length;
    failuresRemaining = 2;
    const { dikamahaRequest } = await import("@/lib/dikamaha");
    await expect(dikamahaRequest(
      "/v1/predict/upcoming", { method: "POST", body: "{}" }, true,
    )).resolves.toMatchObject({ count: 1 });
    expect(requests.length - before).toBe(3);
  });

  it("never retries a POST that is not explicitly marked idempotent", async () => {
    const before = requests.length;
    failuresRemaining = 2;
    const { dikamahaRequest, DikamahaError } = await import("@/lib/dikamaha");
    await expect(dikamahaRequest(
      "/v1/predict/upcoming", { method: "POST", body: "{}" },
    )).rejects.toBeInstanceOf(DikamahaError);
    expect(requests.length - before).toBe(1);
  });

  // Reporte real: Paris Saint-Germain vs Aston Villa (Supercopa de Europa) no
  // mostraba ninguna predicción live. La API sí distinguía la razón exacta
  // (`league_history_below_minimum`), pero `upstreamReason` prefería el
  // código grueso de tres valores sobre el mensaje específico, así que todo
  // 422 de contrato llegaba a la interfaz como "contract_validation_error" y
  // nunca se distinguía nada.
  it("surfaces the specific rejection reason instead of the coarse error bucket", async () => {
    nextContractRejection = "league_history_below_minimum";
    const { dikamahaRequest, DikamahaError } = await import("@/lib/dikamaha");
    await expect(dikamahaRequest(
      "/v1/predict/live/fixture", { method: "POST", body: "{}" },
    )).rejects.toMatchObject(
      new DikamahaError(422, "dikamaha_request_failed", "league_history_below_minimum"),
    );
  });
});
