import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

/**
 * Los endpoints internos son la segunda superficie del proyecto que no usa
 * sesión -la primera fue `/s/<token>`-, y la única que escribe. Su único
 * portero es un secreto compartido, así que conviene probarlo por separado del
 * código que protege.
 */

const BASE_ENV = {
  NODE_ENV: "test",
  TELEGRAM_BOT_TOKEN: "123456789:abcdefghijklmnopqrstuvwxyzABCDE",
  TELEGRAM_ACCESS_MODE: "private",
  TELEGRAM_ALLOWED_USER_IDS: "42",
  DIKAMAHA_BOT_API_URL: "https://api.example.test",
  DIKAMAHA_API_KEY: "1234567890abcdef",
  DATABASE_URL: "postgres://test:test@example.test:5432/test",
  MINIAPP_SESSION_SECRET: "0123456789abcdef0123456789abcdef",
  MINIAPP_INTERNAL_API_KEY: "internal-key-0123456789abcdef0123",
  MINIAPP_BILLING_SECRET: "billing-secret-0123456789abcdef01",
  MINIAPP_ENABLED: "true",
};

async function load() {
  vi.resetModules();
  Object.assign(process.env, BASE_ENV);
  return await import("@/lib/http");
}

function request(key: string | null): NextRequest {
  const headers = new Headers();
  if (key !== null) headers.set("x-dikamaha-internal-key", key);
  return new NextRequest("https://miniapp.test/api/internal/entitlement", { headers });
}

beforeEach(() => {
  vi.resetModules();
});

describe("portero de los endpoints internos", () => {
  it("acepta la clave correcta", async () => {
    const { requireInternalKey } = await load();

    expect(() => requireInternalKey(request(BASE_ENV.MINIAPP_INTERNAL_API_KEY)))
      .not.toThrow();
  });

  it("rechaza cuando falta la cabecera", async () => {
    const { requireInternalKey } = await load();

    expect(() => requireInternalKey(request(null)))
      .toThrow("internal_auth_rejected");
  });

  it("rechaza una clave más corta sin lanzar por longitudes distintas", async () => {
    // `timingSafeEqual` exige buffers del mismo tamaño y lanza si no lo son.
    // Dejar escapar ese throw convertiría una clave equivocada en un 500 -que
    // además distingue "longitud mala" de "clave mala" por el tipo de error-.
    const { requireInternalKey } = await load();

    expect(() => requireInternalKey(request("corta")))
      .toThrow("internal_auth_rejected");
  });

  it("rechaza una clave de igual longitud pero distinta", async () => {
    const { requireInternalKey } = await load();
    const wrong = "X".repeat(BASE_ENV.MINIAPP_INTERNAL_API_KEY.length);

    expect(() => requireInternalKey(request(wrong)))
      .toThrow("internal_auth_rejected");
  });

  it("el rechazo se publica como 401 sin detalle", async () => {
    const { authError } = await load();

    const response = authError(new Error("internal_auth_rejected"));

    expect(response.status).toBe(401);
    // Nada que permita distinguir "clave mala" de "ruta inexistente".
    expect(await response.json()).toEqual({ error: "unauthorized" });
  });
});

describe("códigos de la puerta de pago", () => {
  it("premium y cupo agotado se publican como 402, no como 403", async () => {
    // El cliente tiene que poder distinguir "no puedes" de "todavía no has
    // pagado": son dos pantallas distintas.
    const { authError } = await load();

    expect(authError(new Error("premium_required")).status).toBe(402);
    expect(authError(new Error("prediction_quota_exhausted")).status).toBe(402);
    expect(authError(new Error("csrf_rejected")).status).toBe(403);
    expect(authError(new Error("admin_required")).status).toBe(403);
  });
});
