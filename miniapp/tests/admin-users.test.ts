import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Panel de administración de cuentas.
 *
 * `/api/admin/users` y `/api/admin/users/[userId]` son la única forma de ver
 * y cambiar plan/rol/estado sin salir a SQL directo contra Railway. Lo crítico
 * a probar no es el listado -es un `SELECT` sin lógica-, sino que un no
 * administrador no puede ni leerlo ni escribirlo, y que nadie puede quitarse
 * a sí mismo el rol de administrador desde aquí.
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
  MINIAPP_INTERNAL_API_KEY: "0123456789abcdef0123456789abcdef",
  MINIAPP_BILLING_SECRET: "0123456789abcdef0123456789abcdef",
  MINIAPP_ENABLED: "true",
  // Sin esto `resolveEntitlement` no toca la base y devuelve `role: "user"`
  // fijo -el estado previo a la Fase 125-, así que cualquier fila de admin
  // simulada quedaría ignorada.
  MINIAPP_BILLING_ENABLED: "true",
};

function userListRow() {
  return {
    telegram_user_id: 7, username: "cruz", first_name: "Cruz",
    status: "active", role: "user", plan: "free", plan_source: "default",
    plan_expires_at: null,
    first_seen_at: "2026-08-01T00:00:00Z", last_seen_at: "2026-08-17T00:00:00Z",
  };
}

async function setup(entitlementRow: Record<string, unknown> | null, sessionUserId = 42) {
  const executed: unknown[] = [];
  vi.resetModules();
  Object.assign(process.env, BASE_ENV);
  let token = "";
  vi.doMock("next/headers", () => ({
    cookies: async () => ({ get: () => ({ value: token }) }),
  }));
  vi.doMock("@/lib/db", () => ({
    database: () => ({
      // La primera consulta de cada petición es siempre la resolución de
      // titularidad de quien llama; cualquiera posterior -listar cuentas,
      // aplicar un cambio- pertenece a la ruta y usa una forma distinta de
      // fila, así que no pueden compartir el mismo mock.
      execute: async (query: unknown) => {
        executed.push(query);
        if (executed.length === 1) {
          return entitlementRow ? [entitlementRow] : [];
        }
        return entitlementRow ? [userListRow()] : [];
      },
    }),
  }));
  const { issueSession } = await import("@/lib/auth/session");
  const issued = issueSession({ userId: sessionUserId, firstName: "Marco" });
  token = issued.token;
  const { NextRequest } = await import("next/server");
  return { NextRequest, executed, csrf: issued.session.csrf };
}

function patchRequest(
  NextRequest: typeof import("next/server").NextRequest,
  url: string, body: unknown, csrf: string,
) {
  return new NextRequest(url, {
    method: "PATCH", body: JSON.stringify(body),
    headers: { "x-csrf-token": csrf },
  });
}

function adminRow() {
  return { role: "admin", plan_source: "admin", plan_expires_at: null, effective_plan: "premium" };
}

function userRow() {
  return { role: "user", plan_source: "default", plan_expires_at: null, effective_plan: "free" };
}

beforeEach(() => {
  vi.resetModules();
});

describe("GET /api/admin/users", () => {
  it("rechaza a quien no es administrador", async () => {
    const { NextRequest } = await setup(userRow());
    const { GET } = await import("@/app/api/admin/users/route");

    const response = await GET(new NextRequest("http://mini.local/api/admin/users"));

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({ error: "admin_required" });
  });

  it("devuelve la lista a un administrador", async () => {
    const { NextRequest } = await setup(adminRow());
    const { GET } = await import("@/app/api/admin/users/route");

    const response = await GET(new NextRequest("http://mini.local/api/admin/users"));

    expect(response.status).toBe(200);
  });
});

describe("PATCH /api/admin/users/[userId]", () => {
  it("rechaza a quien no es administrador", async () => {
    const { NextRequest, csrf } = await setup(userRow());
    const { PATCH } = await import("@/app/api/admin/users/[userId]/route");

    const response = await PATCH(
      patchRequest(NextRequest, "http://mini.local/api/admin/users/7", { status: "active" }, csrf),
      { params: Promise.resolve({ userId: "7" }) },
    );

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({ error: "admin_required" });
  });

  it("impide que un administrador se quite el rol a sí mismo", async () => {
    const { NextRequest, csrf } = await setup(adminRow());
    const { PATCH } = await import("@/app/api/admin/users/[userId]/route");

    const response = await PATCH(
      patchRequest(NextRequest, "http://mini.local/api/admin/users/42", { role: "user" }, csrf),
      { params: Promise.resolve({ userId: "42" }) },
    );

    expect(response.status).toBe(409);
  });

  it("aplica un cambio de plan válido", async () => {
    const { NextRequest, executed, csrf } = await setup(adminRow());
    const { PATCH } = await import("@/app/api/admin/users/[userId]/route");

    const response = await PATCH(
      patchRequest(NextRequest, "http://mini.local/api/admin/users/7", { plan: "premium" }, csrf),
      { params: Promise.resolve({ userId: "7" }) },
    );

    expect(response.status).toBe(200);
    // Al menos la lectura de titularidad del propio admin, más el UPDATE de
    // plan sobre el objetivo.
    expect(executed.length).toBeGreaterThanOrEqual(2);
  });

  it("rechaza un cuerpo vacío", async () => {
    const { NextRequest, csrf } = await setup(adminRow());
    const { PATCH } = await import("@/app/api/admin/users/[userId]/route");

    const response = await PATCH(
      patchRequest(NextRequest, "http://mini.local/api/admin/users/7", {}, csrf),
      { params: Promise.resolve({ userId: "7" }) },
    );

    expect(response.status).toBe(422);
  });
});
