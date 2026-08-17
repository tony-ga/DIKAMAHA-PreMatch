import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

beforeAll(() => {
  Object.assign(process.env, {
    TELEGRAM_BOT_TOKEN: "123456789:abcdefghijklmnopqrstuvwxyzABCDE",
    TELEGRAM_ACCESS_MODE: "private",
    TELEGRAM_ALLOWED_USER_IDS: "42",
    DIKAMAHA_BOT_API_URL: "https://api.example.test",
    DIKAMAHA_API_KEY: "1234567890abcdef",
    DATABASE_URL: "postgres://test:test@example.test:5432/test",
    MINIAPP_SESSION_SECRET: "0123456789abcdef0123456789abcdef",
  });
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("signed Mini App sessions", () => {
  it("round-trips and rejects tampering", async () => {
    const { issueSession, parseSession, sessionCookieOptions, validCsrf } = await import("@/lib/auth/session");
    const issued = issueSession({ userId: 42, firstName: "Marco", username: "tester" });
    expect(parseSession(issued.token)).toMatchObject({ userId: 42, firstName: "Marco" });
    expect(parseSession(`${issued.token}x`)).toBeNull();
    expect(validCsrf(issued.session, issued.session.csrf)).toBe(true);
    expect(validCsrf(issued.session, "wrong")).toBe(false);
    expect(sessionCookieOptions()).toMatchObject({ httpOnly: true, sameSite: "lax" });
  });

  it("uses a secure partitioned cookie inside Telegram Web and Desktop", async () => {
    vi.stubEnv("NODE_ENV", "production");
    const { sessionCookieOptions } = await import("@/lib/auth/session");
    expect(sessionCookieOptions()).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "none",
      partitioned: true,
      path: "/",
    });
  });

  it("lasts thirty days so reopening never repeats the Telegram sign-in", async () => {
    const { issueSession, sessionCookieOptions } = await import("@/lib/auth/session");
    const thirtyDays = 30 * 24 * 60 * 60;
    const issued = issueSession({ userId: 42, firstName: "Marco" });
    const lifetime = issued.session.expiresAt - Math.floor(Date.now() / 1000);

    // Con 12 horas, abrir la Mini App dos veces en un día ya obligaba a rehacer
    // el alta completa antes de poder pedir el primer dato.
    expect(lifetime).toBeGreaterThan(thirtyDays - 5);
    expect(sessionCookieOptions().maxAge).toBe(thirtyDays);
  });

  it("only re-issues the cookie once the session is a day old", async () => {
    const { issueSession, parseSession, refreshedSessionToken } =
      await import("@/lib/auth/session");
    const issued = issueSession({ userId: 42, firstName: "Marco" });

    // Recién emitida no hay nada que renovar: escribir `Set-Cookie` en cada
    // respuesta sería puro coste.
    expect(refreshedSessionToken(issued.session)).toBeNull();

    const aged = { ...issued.session, expiresAt: issued.session.expiresAt - 2 * 24 * 60 * 60 };
    const renewed = refreshedSessionToken(aged);
    expect(renewed).not.toBeNull();
    const parsed = parseSession(renewed!);
    expect(parsed).toMatchObject({ userId: 42, firstName: "Marco" });
    // El token CSRF sobrevive a la renovación: el cliente ya lo tiene en
    // memoria y cambiarlo rompería sus mutaciones en vuelo.
    expect(parsed?.csrf).toBe(issued.session.csrf);
    expect(parsed!.expiresAt).toBeGreaterThan(aged.expiresAt);
  });

  it("refuses to renew a session that is already expired", async () => {
    const { issueSession, refreshedSessionToken } = await import("@/lib/auth/session");
    const issued = issueSession({ userId: 42, firstName: "Marco" });
    const dead = { ...issued.session, expiresAt: Math.floor(Date.now() / 1000) - 1 };

    // Una sesión vencida se rehace, no se renueva. Sin esta guarda la función
    // sabría resucitar sesiones muertas con sólo llamarla.
    expect(refreshedSessionToken(dead)).toBeNull();
  });

  it("carries the plan without re-reading it when the cookie is renewed", async () => {
    // Esto documenta el problema que obligó a separar titularidad de sesión:
    // la renovación copia `plan` tal cual, así que una cookie emitida cuando
    // alguien era premium lo seguirá diciendo durante 30 días aunque haya
    // cancelado al día siguiente.
    const { issueSession, parseSession, refreshedSessionToken } =
      await import("@/lib/auth/session");
    const issued = issueSession({ userId: 42, firstName: "Marco", plan: "premium" });
    const aged = { ...issued.session, expiresAt: issued.session.expiresAt - 2 * 24 * 60 * 60 };

    const parsed = parseSession(refreshedSessionToken(aged)!);

    expect(parsed?.plan).toBe("premium");
  });

  it("never lets the cookie plan authorize a paid feature", async () => {
    // La contrapartida de la prueba anterior: el `plan` de la cookie es una
    // pista para el primer pintado y nada más. Quien decide es la fila, así
    // que una cookie que dice "premium" sobre una cuenta gratuita no abre
    // ninguna función de pago.
    const { issueSession } = await import("@/lib/auth/session");
    const issued = issueSession({ userId: 42, firstName: "Marco", plan: "premium" });
    expect(issued.session.plan).toBe("premium");

    vi.resetModules();
    process.env.MINIAPP_BILLING_ENABLED = "true";
    vi.doMock("@/lib/db", () => ({
      database: () => ({
        execute: async () => [{
          role: "user", plan_source: "default",
          plan_expires_at: null, effective_plan: "free",
        }],
      }),
    }));
    const { requireFeature, resolveEntitlement } =
      await import("@/lib/auth/entitlements");

    const entitlement = await resolveEntitlement(issued.session.userId);

    expect(entitlement.plan).toBe("free");
    expect(() => requireFeature(entitlement, "live")).toThrow("premium_required");
    delete process.env.MINIAPP_BILLING_ENABLED;
  });
});
