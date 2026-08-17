import { afterEach, describe, expect, it, vi } from "vitest";

import { signBillingPayload } from "@/lib/billing/payload";
import { reconcileStarTransactions } from "@/worker/billing-reconcile";

/**
 * La red de seguridad.
 *
 * Un `successful_payment` perdido deniega en silencio a alguien que pagó, y es
 * el único fallo del cobro que el usuario no puede diagnosticar. Estas pruebas
 * cubren que reprocesar es inofensivo, que sólo se reparan cargos con firma
 * válida, y que un fallo de Telegram no escapa hacia el bucle de alertas.
 */

const SECRET = "reconcile-secret-0123456789abcdef";

function payloadFor(userId: number): string {
  return signBillingPayload(
    { userId, planCode: "premium_monthly", starsAmount: 250 }, SECRET);
}

/** Fake del cliente `postgres` con libro mayor en memoria. */
function fakeSql(ledger: Set<string>) {
  const run = (strings: TemplateStringsArray, ...values: unknown[]) => {
    const text = strings.join("?");
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
  return sql;
}

function stubTelegram(pages: unknown[][]) {
  let call = 0;
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true,
    json: async () => ({
      ok: true,
      result: { transactions: pages[call++] ?? [] },
    }),
  })));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reconciliación de cargos Stars", () => {
  it("repara sólo el cargo que faltaba", async () => {
    const ledger = new Set<string>(["ya_asentado"]);
    stubTelegram([[
      {
        id: "ya_asentado", amount: 250,
        source: { type: "user", user: { id: 42 }, invoice_payload: payloadFor(42) },
      },
      {
        id: "perdido", amount: 250,
        source: { type: "user", user: { id: 77 }, invoice_payload: payloadFor(77) },
      },
    ], []]);

    const report = await reconcileStarTransactions(fakeSql(ledger) as never, {
      botToken: "token", billingSecret: SECRET,
    });

    expect(report.scanned).toBe(2);
    // `repaired > 0` es una incidencia: significa que el reenvío del bot está
    // perdiendo pagos.
    expect(report.repaired).toBe(1);
    expect(ledger.has("perdido")).toBe(true);
  });

  it("ignora una factura cuya firma no verifica", async () => {
    // Una factura ajena o fabricada a mano no puede conceder un plan.
    const ledger = new Set<string>();
    stubTelegram([[{
      id: "falsa", amount: 250,
      source: { type: "user", user: { id: 42 }, invoice_payload: "v1.42.x.250.1.n.mala" },
    }]]);

    const report = await reconcileStarTransactions(fakeSql(ledger) as never, {
      botToken: "token", billingSecret: SECRET,
    });

    expect(report.repaired).toBe(0);
    expect(ledger.size).toBe(0);
  });

  it("ignora una factura emitida a nombre de otro usuario", async () => {
    const ledger = new Set<string>();
    stubTelegram([[{
      id: "cruzada", amount: 250,
      // Firma válida, pero de otra persona: el pagador no es el titular.
      source: { type: "user", user: { id: 99 }, invoice_payload: payloadFor(42) },
    }]]);

    const report = await reconcileStarTransactions(fakeSql(ledger) as never, {
      botToken: "token", billingSecret: SECRET,
    });

    expect(report.repaired).toBe(0);
  });

  it("aplica reembolsos entrantes", async () => {
    const ledger = new Set<string>();
    stubTelegram([[{
      id: "devuelto", amount: 250,
      receiver: { type: "user", user: { id: 42 } },
    }], []]);

    const report = await reconcileStarTransactions(fakeSql(ledger) as never, {
      botToken: "token", billingSecret: SECRET,
    });

    expect(report.repaired).toBe(1);
    expect(ledger.has("refund:devuelto")).toBe(true);
  });

  it("reprocesar la misma página no repara nada la segunda vez", async () => {
    // Es la propiedad que permite ejecutar esto cada 15 minutos sin estado
    // propio: sin cursor que persistir ni "último visto" que corromper.
    const ledger = new Set<string>();
    const page = [{
      id: "unico", amount: 250,
      source: { type: "user", user: { id: 42 }, invoice_payload: payloadFor(42) },
    }];
    stubTelegram([page, []]);
    await reconcileStarTransactions(fakeSql(ledger) as never, {
      botToken: "token", billingSecret: SECRET,
    });

    vi.unstubAllGlobals();
    stubTelegram([page, []]);
    const second = await reconcileStarTransactions(fakeSql(ledger) as never, {
      botToken: "token", billingSecret: SECRET,
    });

    expect(second.repaired).toBe(0);
  });

  it("deja de paginar cuando una página no repara nada", async () => {
    const ledger = new Set<string>(["a"]);
    const fetchSpy = vi.fn(async () => ({
      ok: true,
      json: async () => ({
        ok: true,
        result: {
          transactions: [{
            id: "a", amount: 250,
            source: { type: "user", user: { id: 42 }, invoice_payload: payloadFor(42) },
          }],
        },
      }),
    }));
    vi.stubGlobal("fetch", fetchSpy);

    await reconcileStarTransactions(fakeSql(ledger) as never, {
      botToken: "token", billingSecret: SECRET,
    });

    // Sólo se profundiza cuando la página anterior reparó algo: "vamos por
    // detrás" es justo el caso en que mirar más atrás está justificado.
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("propaga un fallo de Telegram como error, no como reparación silenciosa", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false, json: async () => ({ ok: false, description: "unauthorized" }),
    })));

    await expect(reconcileStarTransactions(fakeSql(new Set()) as never, {
      botToken: "token", billingSecret: SECRET,
    })).rejects.toThrow("get_star_transactions_failed");
  });
});
