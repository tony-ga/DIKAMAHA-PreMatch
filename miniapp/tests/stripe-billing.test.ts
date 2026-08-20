import { createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";

import {
  applyStripeSubscription,
  applyStripeTermination,
  gatewayStatus,
} from "@/lib/billing/stripe-apply";
import { verifyStripeSignature } from "@/lib/billing/stripe";

/**
 * Cobro web con Stripe (Fase 133, DEC-220).
 *
 * Dos propiedades sostienen esta pasarela, y son las que se prueban aquí:
 *
 * 1. El asiento en `stripe_events` es la **puerta**. Stripe reintenta los
 *    webhooks hasta recibir un 2xx y puede entregar el mismo evento más de una
 *    vez sin que nada haya fallado, así que aplicar dos veces tiene que ser
 *    imposible por construcción y no por cuidado del manejador.
 * 2. Una suscripción viva por usuario. `plan_expires_at` es una sola fecha, y
 *    dos pasarelas activas la pisarían: el usuario pagaría dos veces el mismo
 *    mes sin que el sistema lo notara.
 */

type Statement = { text: string; values: unknown[] };

/**
 * Fake mínimo del cliente `postgres`, hermano del de `billing-apply.test.ts`:
 * registra sentencias y responde al INSERT del libro mayor según si el evento
 * ya estaba asentado.
 */
function fakeSql(ledger: Set<string>, rows: Record<string, unknown[]> = {}) {
  const statements: Statement[] = [];
  const run = (strings: TemplateStringsArray, ...values: unknown[]) => {
    const text = strings.join("?");
    statements.push({ text, values });
    if (text.includes('INSERT INTO "stripe_events"')) {
      const eventId = String(values[0]);
      if (ledger.has(eventId)) return Promise.resolve([]);
      ledger.add(eventId);
      return Promise.resolve([{ event_id: eventId }]);
    }
    for (const [fragment, result] of Object.entries(rows)) {
      if (text.includes(fragment)) return Promise.resolve(result);
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

const SUBSCRIPTION = {
  userId: 42,
  eventId: "evt_1",
  eventType: "checkout.session.completed",
  subscriptionId: "sub_1",
  customerId: "cus_1",
  priceId: "price_1",
  currentPeriodEnd: Math.floor(Date.now() / 1000) + 2_592_000,
};

describe("asiento de una suscripción Stripe", () => {
  it("el primer evento activa el plan", async () => {
    const { sql, statements } = fakeSql(new Set());

    const outcome = await applyStripeSubscription(sql as never, SUBSCRIPTION);

    expect(outcome.applied).toBe(true);
    expect(statements.some((s) => s.text.includes("stripe_subscriptions"))).toBe(true);
    const grant = statements.find((s) => s.text.includes('UPDATE "miniapp_users"'));
    expect(grant).toBeDefined();
    // La procedencia distingue la pasarela: sin esto un reembolso de Stripe no
    // se podría atribuir, y la exclusión mutua no sería comprobable.
    expect(grant!.text).toContain("'stripe'");
  });

  it("el mismo evento entregado dos veces se aplica una sola vez", async () => {
    const ledger = new Set<string>();
    const first = fakeSql(ledger);
    const second = fakeSql(ledger);

    expect((await applyStripeSubscription(first.sql as never, SUBSCRIPTION)).applied).toBe(true);
    const repeat = await applyStripeSubscription(second.sql as never, SUBSCRIPTION);

    expect(repeat.applied).toBe(false);
    // Y lo importante: el reintento no llegó a tocar el plan.
    expect(second.statements.some((s) => s.text.includes('UPDATE "miniapp_users"'))).toBe(false);
  });

  it("extiende el periodo sin poder acortarlo", async () => {
    const { sql, statements } = fakeSql(new Set());
    await applyStripeSubscription(sql as never, SUBSCRIPTION);
    const grant = statements.find((s) => s.text.includes('UPDATE "miniapp_users"'));
    // Un evento viejo entregado tarde no puede recortar una suscripción ya
    // extendida.
    expect(grant!.text).toContain("GREATEST");
  });
});

describe("terminación de una suscripción Stripe", () => {
  it("la cancelación retira el plan", async () => {
    const { sql, statements } = fakeSql(new Set());

    const outcome = await applyStripeTermination(sql as never, {
      userId: 42, eventId: "evt_2", eventType: "customer.subscription.deleted",
      status: "canceled",
    });

    expect(outcome.applied).toBe(true);
    const update = statements.find((s) => s.text.includes('UPDATE "miniapp_users"'));
    expect(update!.text).toContain("'free'");
  });

  it("no toca el plan de otra pasarela", async () => {
    const { sql, statements } = fakeSql(new Set());

    await applyStripeTermination(sql as never, {
      userId: 42, eventId: "evt_3", eventType: "charge.refunded", status: "refunded",
    });

    // La guarda es lo que impide que un evento de Stripe retire un premium
    // pagado con Stars -y de paso protege al administrador, cuyo premium no
    // viene de ningún cobro-.
    const update = statements.find((s) => s.text.includes('UPDATE "miniapp_users"'));
    expect(update!.text).toContain(`AND "plan_source" = 'stripe'`);
  });

  it("una terminación repetida no vuelve a escribir", async () => {
    const ledger = new Set<string>();
    const first = fakeSql(ledger);
    const second = fakeSql(ledger);
    const input = {
      userId: 42, eventId: "evt_4", eventType: "customer.subscription.deleted",
      status: "canceled" as const,
    };

    await applyStripeTermination(first.sql as never, input);
    const repeat = await applyStripeTermination(second.sql as never, input);

    expect(repeat.applied).toBe(false);
    expect(second.statements.some((s) => s.text.includes('UPDATE "miniapp_users"'))).toBe(false);
  });
});

describe("exclusión mutua entre pasarelas (DEC-220)", () => {
  it("detecta una suscripción Stars viva", async () => {
    const { sql } = fakeSql(new Set(), { '"star_subscriptions"': [{ "?column?": 1 }] });

    expect((await gatewayStatus(sql as never, 42)).stars).toBe(true);
  });

  it("sin suscripciones no bloquea nada", async () => {
    const { sql } = fakeSql(new Set());
    const status = await gatewayStatus(sql as never, 42);

    expect(status.stars).toBe(false);
    expect(status.stripe).toBe(false);
    expect(status.stripeCustomerId).toBeNull();
  });

  it("reutiliza el cliente de Stripe ya creado", async () => {
    // Sin esto, cada checkout abandonado dejaría un cliente nuevo en Stripe y
    // el historial de pagos del usuario quedaría partido.
    const { sql } = fakeSql(new Set(), {
      '"stripe_customers"': [{ stripe_customer_id: "cus_9" }],
    });

    expect((await gatewayStatus(sql as never, 42)).stripeCustomerId).toBe("cus_9");
  });
});

describe("firma del webhook de Stripe", () => {
  const SECRET = "whsec_test_0123456789abcdef";
  const NOW = 1_800_000_000;
  const BODY = '{"id":"evt_1","type":"invoice.paid"}';

  function header(timestamp: number, body = BODY, secret = SECRET): string {
    const signature = createHmac("sha256", secret)
      .update(`${timestamp}.${body}`)
      .digest("hex");
    return `t=${timestamp},v1=${signature}`;
  }

  it("acepta una firma auténtica y reciente", () => {
    expect(verifyStripeSignature(BODY, header(NOW), SECRET, NOW)).toBe(true);
  });

  it("rechaza un cuerpo alterado", () => {
    // El motivo por el que el manejador lee `text()` y nunca `json()`:
    // reserializar cambia el cuerpo y la firma deja de cuadrar.
    expect(verifyStripeSignature(`${BODY} `, header(NOW), SECRET, NOW)).toBe(false);
  });

  it("rechaza otro secreto", () => {
    expect(verifyStripeSignature(BODY, header(NOW, BODY, "whsec_otro"), SECRET, NOW)).toBe(false);
  });

  it("rechaza un evento legítimo reproducido más tarde", () => {
    expect(verifyStripeSignature(BODY, header(NOW), SECRET, NOW + 600)).toBe(false);
  });

  it("rechaza una cabecera ausente o vacía", () => {
    expect(verifyStripeSignature(BODY, null, SECRET, NOW)).toBe(false);
    expect(verifyStripeSignature(BODY, "t=,v1=", SECRET, NOW)).toBe(false);
  });

  it("acepta una de varias firmas durante una rotación de secreto", () => {
    const rotated = `${header(NOW, BODY, "whsec_antiguo")},v1=${
      createHmac("sha256", SECRET).update(`${NOW}.${BODY}`).digest("hex")}`;
    expect(verifyStripeSignature(BODY, rotated, SECRET, NOW)).toBe(true);
  });

  it("no acepta nada sin secreto configurado", () => {
    expect(verifyStripeSignature(BODY, header(NOW), "", NOW)).toBe(false);
  });
});
