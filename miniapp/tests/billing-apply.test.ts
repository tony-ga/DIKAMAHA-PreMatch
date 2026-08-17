import { describe, expect, it } from "vitest";

import { applyStarPayment, applyStarRefund } from "@/lib/billing/apply";

/**
 * Aplicación de cargos.
 *
 * Lo que se prueba aquí es la propiedad que sostiene todo el cobro: el asiento
 * en el libro mayor es la **puerta**, así que reenvío del bot, reintento y
 * reconciliación pueden competir por el mismo cargo sin pisarse.
 */

type Statement = { text: string };

/**
 * Fake mínimo del cliente `postgres`: registra las sentencias y responde al
 * INSERT del libro mayor según si el cargo ya existía.
 */
function fakeSql(ledger: Set<string>) {
  const statements: Statement[] = [];
  const run = (strings: TemplateStringsArray, ...values: unknown[]) => {
    const text = strings.join("?");
    statements.push({ text });
    if (text.includes("INSERT INTO \"star_payments\"")) {
      const chargeId = String(values[0]);
      if (ledger.has(chargeId)) return Promise.resolve([]);
      ledger.add(chargeId);
      return Promise.resolve([{ telegram_payment_charge_id: chargeId }]);
    }
    return Promise.resolve([]);
  };
  const sql = run as unknown as {
    (strings: TemplateStringsArray, ...values: unknown[]): Promise<unknown[]>;
    begin: (fn: (tx: unknown) => Promise<unknown>) => Promise<unknown>;
  };
  sql.begin = (fn) => fn(sql);
  return { sql, statements };
}

const CHARGE = {
  userId: 42,
  chargeId: "charge_1",
  starsAmount: 250,
  invoicePayload: "v1.42.premium_monthly.250.100.nonce.sig",
  isRecurring: false,
  isFirstRecurring: true,
  subscriptionExpirationDate: Math.floor(Date.now() / 1000) + 2_592_000,
};

describe("asiento de un cargo Stars", () => {
  it("el primer cargo activa el plan", async () => {
    const ledger = new Set<string>();
    const { sql, statements } = fakeSql(ledger);

    const outcome = await applyStarPayment(sql as never, CHARGE, "bot_forward");

    expect(outcome.applied).toBe(true);
    expect(statements.some((s) => s.text.includes("star_subscriptions"))).toBe(true);
    expect(statements.some((s) => s.text.includes("UPDATE \"miniapp_users\""))).toBe(true);
  });

  it("repetir el mismo cargo no toca nada más", async () => {
    // Es lo que hace seguro que el bot reintente y que el reconciliador
    // reprocese los últimos cien cargos.
    const ledger = new Set<string>();
    await applyStarPayment(fakeSql(ledger).sql as never, CHARGE, "bot_forward");
    const second = fakeSql(ledger);

    const outcome = await applyStarPayment(second.sql as never, CHARGE, "reconcile");

    expect(outcome).toEqual({ applied: false, reason: "duplicate" });
    // Sale por la puerta: no hay UPDATE de plan en la segunda pasada.
    expect(second.statements.some((s) => s.text.includes("UPDATE \"miniapp_users\"")))
      .toBe(false);
  });

  it("una renovación con id nuevo sí vuelve a aplicar", async () => {
    const ledger = new Set<string>();
    await applyStarPayment(fakeSql(ledger).sql as never, CHARGE, "bot_forward");
    const renewal = fakeSql(ledger);

    const outcome = await applyStarPayment(renewal.sql as never, {
      ...CHARGE, chargeId: "charge_2", isRecurring: true, isFirstRecurring: false,
    }, "bot_forward");

    expect(outcome.applied).toBe(true);
  });

  it("usa GREATEST para que una llegada fuera de orden no acorte el periodo", async () => {
    // El reconciliador puede recoger el cargo N+1 antes de que el bot entregue
    // el N; aplicar el viejo después no puede recortar una suscripción ya
    // extendida.
    const { sql, statements } = fakeSql(new Set());

    await applyStarPayment(sql as never, CHARGE, "reconcile");

    const subscription = statements.find((s) => s.text.includes("star_subscriptions"));
    const user = statements.find((s) => s.text.includes("UPDATE \"miniapp_users\""));
    expect(subscription?.text).toContain("GREATEST");
    expect(user?.text).toContain("GREATEST");
  });
});

describe("reembolsos", () => {
  it("revoca el acceso y respeta a los administradores", async () => {
    const { sql, statements } = fakeSql(new Set());

    const outcome = await applyStarRefund(sql as never, {
      userId: 42, chargeId: "charge_1", starsAmount: 250,
    }, "manual");

    expect(outcome.applied).toBe(true);
    const user = statements.find((s) => s.text.includes("UPDATE \"miniapp_users\""));
    expect(user?.text).toContain("'refunded'");
    // Un administrador no debe quedarse fuera del panel por un reembolso.
    expect(user?.text).toContain("plan_source\" <> 'admin'");
  });

  it("repetir un reembolso es inofensivo", async () => {
    // Telegram entrega `refunded_payment` además del reembolso que inicia el
    // panel, así que el mismo hecho llega dos veces por caminos distintos.
    const ledger = new Set<string>();
    const input = { userId: 42, chargeId: "charge_1", starsAmount: 250 };
    await applyStarRefund(fakeSql(ledger).sql as never, input, "manual");

    const outcome = await applyStarRefund(
      fakeSql(ledger).sql as never, input, "bot_forward");

    expect(outcome).toEqual({ applied: false, reason: "duplicate" });
  });

  it("el reembolso no colisiona con el cargo del mismo id", async () => {
    // Ambos van al mismo libro mayor con la misma clave primaria, así que el
    // reembolso se asienta con prefijo propio o borraría el cargo original.
    const ledger = new Set<string>();
    await applyStarPayment(fakeSql(ledger).sql as never, CHARGE, "bot_forward");

    const outcome = await applyStarRefund(fakeSql(ledger).sql as never, {
      userId: 42, chargeId: "charge_1", starsAmount: 250,
    }, "manual");

    expect(outcome.applied).toBe(true);
  });
});
