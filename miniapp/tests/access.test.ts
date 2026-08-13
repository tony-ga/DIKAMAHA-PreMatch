import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * El acceso lo decidía `TELEGRAM_ALLOWED_USER_IDS`, una variable de entorno, de
 * modo que dar de alta a una persona exigía **redesplegar el servicio**. Estas
 * pruebas cubren que ahora lo decide la fila de `miniapp_users` y que la
 * variable queda sólo como semilla de administradores.
 */

type Row = { status: string; role: string; plan: string } | null;

const BASE_ENV = {
  NODE_ENV: "test",
  TELEGRAM_BOT_TOKEN: "123456789:abcdefghijklmnopqrstuvwxyzABCDE",
  TELEGRAM_ACCESS_MODE: "private",
  TELEGRAM_ALLOWED_USER_IDS: "42",
  DIKAMAHA_BOT_API_URL: "https://api.example.test",
  DIKAMAHA_API_KEY: "1234567890abcdef",
  DATABASE_URL: "postgres://test:test@example.test:5432/test",
  MINIAPP_SESSION_SECRET: "0123456789abcdef0123456789abcdef",
  MINIAPP_ENABLED: "true",
  MINIAPP_ALERTS_ENABLED: "false",
};

/** Reproduce lo justo de la cadena de drizzle que usa `resolveAccess`. */
function stubDatabase(row: Row, updates: Record<string, unknown>[]) {
  return () => ({
    select: () => ({
      from: () => ({ where: () => ({ limit: async () => (row ? [row] : []) }) }),
    }),
    update: () => ({
      set: (values: Record<string, unknown>) => {
        updates.push(values);
        return { where: () => ({ returning: async () => (row ? [{ plan: row.plan }] : []) }) };
      },
    }),
  });
}

async function loadAccess(row: Row, environment: Record<string, string> = {}) {
  const updates: Record<string, unknown>[] = [];
  vi.resetModules();
  Object.assign(process.env, BASE_ENV, environment);
  vi.doMock("@/lib/db", () => ({ database: stubDatabase(row, updates) }));
  const module = await import("@/lib/auth/access");
  return { ...module, updates };
}

beforeEach(() => {
  vi.resetModules();
});

describe("account based access control", () => {
  it("lets an approved account in without touching the environment allowlist", async () => {
    const { resolveAccess } = await loadAccess(
      { status: "active", role: "user", plan: "free" });

    const decision = await resolveAccess(777);

    // 777 no está en `TELEGRAM_ALLOWED_USER_IDS`: entra porque su fila lo dice.
    // Ese es todo el cambio -alta sin redesplegar-.
    expect(decision).toEqual({
      granted: true,
      account: { status: "active", role: "user", plan: "free" },
    });
  });

  it("holds a pending account without confusing it with a rejection", async () => {
    const { resolveAccess } = await loadAccess(
      { status: "pending", role: "user", plan: "free" });

    // Distinguir "esperando aprobación" de "denegado" es lo que permite decirle
    // al usuario si debe pedir el alta o dejar de intentarlo.
    expect(await resolveAccess(777)).toEqual({
      granted: false, reason: "access_pending",
    });
  });

  it("keeps a blocked account out", async () => {
    const { resolveAccess } = await loadAccess(
      { status: "blocked", role: "user", plan: "free" });

    expect(await resolveAccess(777)).toEqual({
      granted: false, reason: "access_blocked",
    });
  });

  it("treats an unknown account as pending instead of failing", async () => {
    const { resolveAccess } = await loadAccess(null);

    expect(await resolveAccess(999)).toEqual({
      granted: false, reason: "access_pending",
    });
  });

  it("always admits the environment seed and records it as administrator", async () => {
    const { resolveAccess, updates } = await loadAccess(
      { status: "pending", role: "user", plan: "free" });

    const decision = await resolveAccess(42);

    // La semilla entra aunque su fila diga `pending`: si un error de datos
    // dejara a todo el mundo fuera, nadie podría aprobar a nadie.
    expect(decision).toMatchObject({ granted: true, account: { role: "admin" } });
    // Y queda escrito, para que el estado real sea visible en la tabla en vez
    // de ser una autorización invisible que sólo existe en la configuración.
    expect(updates).toHaveLength(1);
    expect(updates[0]).toMatchObject({ status: "active", role: "admin" });
  });

  it("auto activates new accounts only in public mode", async () => {
    const closed = await loadAccess(null);
    expect(closed.autoActivates()).toBe(false);

    const open = await loadAccess(null, { TELEGRAM_ACCESS_MODE: "public" });
    expect(open.autoActivates()).toBe(true);
  });
});
