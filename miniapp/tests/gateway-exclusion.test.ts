import { describe, expect, it } from "vitest";

import { subscriptionBlock } from "@/lib/billing/gateways";

/**
 * Una suscripción viva por usuario (DEC-220), en **las tres puertas**.
 *
 * Existe por un fallo real encontrado en auditoría: la comprobación estaba
 * escrita dentro del módulo de Stripe y sólo la llamaba el checkout de la web.
 * Las otras dos puertas -la factura Stars de la Mini App y la que pide el bot-
 * no la llamaban, así que quien pagaba con tarjeta podía comprar Stars encima y
 * pagar dos veces el mismo mes. Ambas escriben sobre el mismo
 * `plan_expires_at`, de modo que el sistema no podía ni notarlo.
 *
 * Lo que estas pruebas fijan es la **simetría**: da igual desde qué pasarela se
 * pida, una suscripción viva en cualquiera de las dos bloquea.
 */

type Rows = { stars?: boolean; stripe?: boolean; customer?: string };

function fakeSql(rows: Rows) {
  const run = (strings: TemplateStringsArray) => {
    const text = strings.join("?");
    if (text.includes('"star_subscriptions"')) {
      return Promise.resolve(rows.stars ? [{ "?column?": 1 }] : []);
    }
    if (text.includes('"stripe_subscriptions"')) {
      return Promise.resolve(rows.stripe ? [{ "?column?": 1 }] : []);
    }
    if (text.includes('"stripe_customers"')) {
      return Promise.resolve(rows.customer ? [{ stripe_customer_id: rows.customer }] : []);
    }
    return Promise.resolve([]);
  };
  return run as never;
}

describe("exclusión mutua entre pasarelas", () => {
  it("sin suscripciones, cualquier pasarela puede empezar", async () => {
    expect(await subscriptionBlock(fakeSql({}), 42, "stars")).toBeNull();
    expect(await subscriptionBlock(fakeSql({}), 42, "stripe")).toBeNull();
  });

  it("con Stripe vivo, la factura de Stars queda bloqueada", async () => {
    // Ésta es exactamente la dirección que faltaba y permitía el doble cobro.
    expect(await subscriptionBlock(fakeSql({ stripe: true }), 42, "stars"))
      .toBe("stripe_subscription_active");
  });

  it("con Stars vivo, el checkout de Stripe queda bloqueado", async () => {
    expect(await subscriptionBlock(fakeSql({ stars: true }), 42, "stripe"))
      .toBe("stars_subscription_active");
  });

  it("distingue 'ya tienes ésta' de 'tienes la otra'", async () => {
    // No es cosmético: sólo el segundo caso necesita decirle al usuario dónde
    // está lo que ya paga.
    expect(await subscriptionBlock(fakeSql({ stars: true }), 42, "stars"))
      .toBe("already_subscribed");
    expect(await subscriptionBlock(fakeSql({ stripe: true }), 42, "stripe"))
      .toBe("already_subscribed");
  });

  it("una suscripción cancelada o vencida no bloquea una compra nueva", async () => {
    // El `WHERE` filtra por estado activo y periodo vigente, así que quien
    // canceló hace meses puede volver. Sin esto, un ex-suscriptor no podría
    // pagar nunca más.
    expect(await subscriptionBlock(fakeSql({}), 42, "stars")).toBeNull();
    expect(await subscriptionBlock(fakeSql({}), 42, "stripe")).toBeNull();
  });
});
