import { describe, expect, it } from "vitest";

import { quotaBannerVisible, sellableQuota } from "@/lib/premium-banner";

/**
 * Cuándo se ofrece Premium fuera del muro.
 *
 * Estas reglas deciden si el producto pide dinero o se calla, así que se
 * prueban aparte de la interfaz: un `if` reordenado en un componente no falla
 * ninguna prueba visual, pero puede poner un botón de compra delante de alguien
 * que ya paga -o de alguien a quien no se le está gateando nada-.
 */

const QUOTA = { used: 2, limit: 3, remaining: 1 };

describe("a quién se le ofrece Premium", () => {
  it("a un usuario gratuito con cuota conocida", () => {
    expect(sellableQuota({ plan: "free", enforced: true, quota: QUOTA })).toEqual(QUOTA);
  });

  it("a nadie mientras el cobro está apagado", () => {
    // `enforced: false` significa que nada está gateado. Ofrecer Premium ahí
    // sería cobrar por lo que se está regalando.
    expect(sellableQuota({ plan: "free", enforced: false, quota: QUOTA })).toBeNull();
  });

  it("a nadie que ya sea premium", () => {
    expect(sellableQuota({ plan: "premium", enforced: true, quota: null })).toBeNull();
  });

  it("a nadie mientras no se sabe el plan", () => {
    // Pintar y despintar es peor que esperar medio segundo.
    expect(sellableQuota(undefined)).toBeNull();
  });

  it("a nadie sin cuota que mostrar", () => {
    expect(sellableQuota({ plan: "free", enforced: true, quota: null })).toBeNull();
  });
});

describe("cuándo el aviso de cuota merece espacio", () => {
  it("con ninguna restante", () => {
    expect(quotaBannerVisible({ used: 3, limit: 3, remaining: 0 })).toBe(true);
  });

  it("con una restante", () => {
    expect(quotaBannerVisible({ used: 2, limit: 3, remaining: 1 })).toBe(true);
  });

  it("no con el día entero por delante", () => {
    // Un banner que sale siempre deja de leerse, y entonces tampoco se ve el
    // día que sí importa.
    expect(quotaBannerVisible({ used: 0, limit: 3, remaining: 3 })).toBe(false);
    expect(quotaBannerVisible({ used: 1, limit: 3, remaining: 2 })).toBe(false);
  });

  it("no cuando no hay nada que vender", () => {
    expect(quotaBannerVisible(null)).toBe(false);
  });
});
