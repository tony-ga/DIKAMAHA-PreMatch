import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * El cupo diario del plan gratuito.
 *
 * La parte mockeable es el mapeo de resultados; la parte que de verdad importa
 * -que dos peticiones simultáneas no puedan pasarse del tope- sólo se puede
 * probar contra un PostgreSQL real, y ese bloque está al final.
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
  MINIAPP_FREE_DAILY_PREDICTIONS: "3",
};

type Reply = Record<string, unknown>;

async function load(replies: Reply[]) {
  const queue = [...replies];
  const calls: unknown[] = [];
  vi.resetModules();
  Object.assign(process.env, BASE_ENV);
  vi.doMock("@/lib/db", () => ({
    database: () => ({
      execute: async (query: unknown) => {
        calls.push(query);
        return queue.length ? [queue.shift()] : [];
      },
    }),
  }));
  const module = await import("@/lib/billing/quota");
  return { ...module, calls };
}

beforeEach(() => {
  vi.resetModules();
});

describe("consumo del cupo diario", () => {
  it("premium no toca la base en absoluto", async () => {
    const { consumePrediction, calls } = await load([]);

    const outcome = await consumePrediction(1, "esp.1:99", "miniapp", { premium: true });

    expect(outcome).toEqual({ granted: true, reason: "premium", remaining: null });
    expect(calls).toHaveLength(0);
  });

  it("la primera predicción del día consume una unidad", async () => {
    const { consumePrediction } = await load([
      { replay: false, used_after: 1, limit_after: 3 },
    ]);

    const outcome = await consumePrediction(1, "esp.1:99", "miniapp");

    expect(outcome).toEqual({ granted: true, reason: "consumed", remaining: 2 });
  });

  it("reabrir el mismo partido no cuesta otra unidad", async () => {
    // Es la razón de existir de `prediction_grants`: la Mini App reconsulta al
    // recuperar el foco, así que contar por petición se comería el cupo solo.
    const { consumePrediction } = await load([
      { replay: true, used_after: null, limit_after: null },
      { used: 1, daily_limit: 3 },
    ]);

    const outcome = await consumePrediction(1, "esp.1:99", "miniapp");

    expect(outcome).toEqual({ granted: true, reason: "replay", remaining: 2 });
  });

  it("agotado cuando el guardián del UPDATE no devuelve filas", async () => {
    // `ON CONFLICT DO UPDATE ... WHERE used < daily_limit` no lanza cuando el
    // WHERE falla: devuelve cero filas. Distinguir eso de un error es lo que
    // hace innecesario el manejo de excepciones en el camino normal.
    const { consumePrediction } = await load([
      { replay: false, used_after: null, limit_after: null },
    ]);

    const outcome = await consumePrediction(1, "esp.1:99", "miniapp");

    expect(outcome).toEqual({ granted: false, reason: "exhausted", remaining: 0 });
  });

  it("publica el tope congelado en la fila, no el de configuración", async () => {
    // Subir el límite a mitad de día no puede encoger -ni ampliar- el día que
    // ya se le prometió al usuario esta mañana.
    const { consumePrediction } = await load([
      { replay: false, used_after: 4, limit_after: 5 },
    ]);

    const outcome = await consumePrediction(1, "esp.1:99", "miniapp");

    expect(outcome).toEqual({ granted: true, reason: "consumed", remaining: 1 });
  });

  it("la instantánea informa cero cuando no hay fila del día", async () => {
    const { quotaSnapshot } = await load([]);

    expect(await quotaSnapshot(1)).toEqual({ used: 0, limit: 3, remaining: 3 });
  });
});

/**
 * Concurrencia real.
 *
 * Es la única aserción que prueba de verdad el diseño: una versión mockeada no
 * dice nada sobre bloqueos de fila. Se omite sola sin `DIKAMAHA_TEST_DATABASE_URL`,
 * igual que los marcadores `postgres` de pytest.
 */
const REAL_DB = process.env.DIKAMAHA_TEST_DATABASE_URL;

describe.skipIf(!REAL_DB)("cupo diario contra PostgreSQL real", () => {
  it("doce peticiones simultáneas conceden exactamente tres", async () => {
    vi.resetModules();
    Object.assign(process.env, BASE_ENV, { DATABASE_URL: REAL_DB });
    const { consumePrediction } = await import("@/lib/billing/quota");
    const { rawDatabase } = await import("@/lib/db");
    const sql = rawDatabase();
    const userId = 999_000_001;

    await sql`
      INSERT INTO miniapp_users (telegram_user_id, first_name, status)
      VALUES (${userId}, 'quota-test', 'active')
      ON CONFLICT (telegram_user_id) DO NOTHING`;
    await sql`DELETE FROM prediction_grants WHERE user_id = ${userId}`;
    await sql`DELETE FROM prediction_quota_days WHERE user_id = ${userId}`;

    const outcomes = await Promise.all(
      Array.from({ length: 12 }, (_, index) =>
        consumePrediction(userId, `esp.1:${index}`, "miniapp")));

    expect(outcomes.filter((row) => row.granted)).toHaveLength(3);
    const [row] = await sql`
      SELECT used FROM prediction_quota_days WHERE user_id = ${userId}`;
    expect(Number(row.used)).toBe(3);

    await sql`DELETE FROM prediction_grants WHERE user_id = ${userId}`;
    await sql`DELETE FROM prediction_quota_days WHERE user_id = ${userId}`;
    await sql`DELETE FROM miniapp_users WHERE telegram_user_id = ${userId}`;
    await sql.end();
  });
});
