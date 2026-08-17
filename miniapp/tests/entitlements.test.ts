import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * La cookie de sesión dura 30 días y no se relee, así que no puede ser la
 * autoridad sobre una suscripción mensual. Estas pruebas cubren que la
 * autoridad es la fila, que un premium caducado se degrada solo, y que el
 * interruptor de cobro apagado no emite ni una consulta.
 */

type Row = {
  role: string;
  plan_source: string;
  plan_expires_at: string | null;
  effective_plan: string;
} | null;

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
};

async function load(row: Row | (() => Row), environment: Record<string, string> = {}) {
  const calls: unknown[] = [];
  vi.resetModules();
  Object.assign(process.env, BASE_ENV, { MINIAPP_BILLING_ENABLED: "true" }, environment);
  vi.doMock("@/lib/db", () => ({
    database: () => ({
      execute: async (query: unknown) => {
        calls.push(query);
        const value = typeof row === "function" ? row() : row;
        if (value === null) return [];
        return [value];
      },
    }),
  }));
  const module = await import("@/lib/auth/entitlements");
  return { ...module, calls };
}

beforeEach(() => {
  vi.resetModules();
});

describe("resolución de titularidad", () => {
  it("con el cobro apagado devuelve premium sin consultar la base", async () => {
    const { resolveEntitlement, calls } = await load(null, {
      MINIAPP_BILLING_ENABLED: "false",
    });

    const entitlement = await resolveEntitlement(777);

    expect(entitlement.plan).toBe("premium");
    expect(entitlement.enforced).toBe(false);
    // Es la garantía del despliegue por pasos: con el interruptor apagado el
    // servicio se comporta exactamente igual que antes de la fase, sin añadir
    // ni un viaje a PostgreSQL.
    expect(calls).toHaveLength(0);
  });

  it("una suscripción vigente es premium", async () => {
    const future = new Date(Date.now() + 864e5).toISOString();
    const { resolveEntitlement } = await load({
      role: "user", plan_source: "stars",
      plan_expires_at: future, effective_plan: "premium",
    });

    const entitlement = await resolveEntitlement(777);

    expect(entitlement.plan).toBe("premium");
    expect(entitlement.enforced).toBe(true);
  });

  it("un premium caducado lee free sin esperar a ningún barrido", async () => {
    // La consulta calcula el plan efectivo en SQL, así que una fila vencida
    // llega ya como 'free'. Sin esto haría falta un barrido, y un barrido que
    // no corra deja premium a quien no paga.
    const past = new Date(Date.now() - 864e5).toISOString();
    const { resolveEntitlement } = await load({
      role: "user", plan_source: "stars",
      plan_expires_at: past, effective_plan: "free",
    });

    expect((await resolveEntitlement(777)).plan).toBe("free");
  });

  it("un administrador es premium y sin caducidad", async () => {
    const { resolveEntitlement } = await load({
      role: "admin", plan_source: "admin",
      plan_expires_at: null, effective_plan: "premium",
    });

    const entitlement = await resolveEntitlement(42);

    expect(entitlement.plan).toBe("premium");
    expect(entitlement.expiresAt).toBeNull();
  });

  it("un fallo de base degrada a free en lugar de lanzar", async () => {
    vi.resetModules();
    Object.assign(process.env, BASE_ENV, { MINIAPP_BILLING_ENABLED: "true" });
    vi.doMock("@/lib/db", () => ({
      database: () => ({
        execute: async () => { throw new Error("connection_refused"); },
      }),
    }));
    const { resolveEntitlement } = await import("@/lib/auth/entitlements");

    const entitlement = await resolveEntitlement(777);

    // Free no es un cierre: conserva historial, catálogo y las 3 del día.
    expect(entitlement.plan).toBe("free");
  });

  it("no cachea una resolución degradada", async () => {
    // Un fallo transitorio no puede fijar 60 segundos de castigo a alguien que
    // sí pagó, así que el segundo intento tiene que volver a consultar.
    let fail = true;
    vi.resetModules();
    Object.assign(process.env, BASE_ENV, { MINIAPP_BILLING_ENABLED: "true" });
    let queries = 0;
    vi.doMock("@/lib/db", () => ({
      database: () => ({
        execute: async () => {
          queries += 1;
          if (fail) throw new Error("connection_refused");
          return [{
            role: "user", plan_source: "stars",
            plan_expires_at: new Date(Date.now() + 864e5).toISOString(),
            effective_plan: "premium",
          }];
        },
      }),
    }));
    const { resolveEntitlement } = await import("@/lib/auth/entitlements");

    expect((await resolveEntitlement(777)).plan).toBe("free");
    fail = false;
    expect((await resolveEntitlement(777)).plan).toBe("premium");
    expect(queries).toBe(2);
  });

  it("cachea una lectura correcta y la olvida al invalidar", async () => {
    const future = new Date(Date.now() + 864e5).toISOString();
    const { resolveEntitlement, invalidateEntitlement, calls } = await load({
      role: "user", plan_source: "stars",
      plan_expires_at: future, effective_plan: "premium",
    });

    await resolveEntitlement(777);
    await resolveEntitlement(777);
    expect(calls).toHaveLength(1);

    // El alta de un pago invalida, para que activar Premium se note ya y no
    // tras el TTL.
    invalidateEntitlement(777);
    await resolveEntitlement(777);
    expect(calls).toHaveLength(2);
  });
});

describe("puertas por función", () => {
  it("free no abre ninguna función de pago y premium las abre todas", async () => {
    const { allows, requireFeature } = await load(null);
    const free = {
      userId: 1, plan: "free" as const, role: "user" as const,
      planSource: "default" as const, expiresAt: null, enforced: true,
    };
    const premium = { ...free, plan: "premium" as const };

    expect(allows(free, "live")).toBe(false);
    expect(allows(free, "high_probability")).toBe(false);
    expect(allows(premium, "live")).toBe(true);
    expect(() => requireFeature(free, "live")).toThrow("premium_required");
    expect(() => requireFeature(premium, "live")).not.toThrow();
  });

  it("sin cobro activo nada queda gateado", async () => {
    const { allows } = await load(null);
    const relaxed = {
      userId: 1, plan: "free" as const, role: "user" as const,
      planSource: "default" as const, expiresAt: null, enforced: false,
    };

    expect(allows(relaxed, "live")).toBe(true);
  });
});
