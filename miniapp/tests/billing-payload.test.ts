import { describe, expect, it } from "vitest";

import { signBillingPayload, verifyBillingPayload } from "@/lib/billing/payload";

const SECRET = "0123456789abcdef0123456789abcdef";
const OTHER = "fedcba9876543210fedcba9876543210";

describe("payload firmado de factura", () => {
  it("ida y vuelta conserva los datos", () => {
    const token = signBillingPayload(
      { userId: 42, planCode: "premium_monthly", starsAmount: 250 }, SECRET);
    const parsed = verifyBillingPayload(token, SECRET);
    expect(parsed).toMatchObject({
      userId: 42, planCode: "premium_monthly", starsAmount: 250,
    });
  });

  it("rechaza un usuario manipulado", () => {
    // El ataque real: coger una factura válida propia y cambiar el id para
    // activarle premium a otra cuenta.
    const token = signBillingPayload(
      { userId: 42, planCode: "premium_monthly", starsAmount: 250 }, SECRET);
    const tampered = token.replace(".42.", ".99.");
    expect(verifyBillingPayload(tampered, SECRET)).toBeNull();
  });

  it("rechaza una firma alterada", () => {
    const token = signBillingPayload(
      { userId: 42, planCode: "premium_monthly", starsAmount: 250 }, SECRET);
    const parts = token.split(".");
    parts[6] = `${parts[6].slice(0, -1)}X`;
    expect(verifyBillingPayload(parts.join("."), SECRET)).toBeNull();
  });

  it("rechaza un token truncado sin lanzar", () => {
    // `timingSafeEqual` lanza si las longitudes difieren; ese throw no puede
    // escapar o una firma corta daría un 500 en vez de un rechazo.
    const token = signBillingPayload(
      { userId: 42, planCode: "premium_monthly", starsAmount: 250 }, SECRET);
    expect(() => verifyBillingPayload(token.slice(0, 20), SECRET)).not.toThrow();
    expect(verifyBillingPayload(token.slice(0, 20), SECRET)).toBeNull();
  });

  it("rechaza otra clave", () => {
    const token = signBillingPayload(
      { userId: 42, planCode: "premium_monthly", starsAmount: 250 }, SECRET);
    expect(verifyBillingPayload(token, OTHER)).toBeNull();
  });

  it("rechaza un titular distinto del pagador", () => {
    const token = signBillingPayload(
      { userId: 42, planCode: "premium_monthly", starsAmount: 250 }, SECRET);
    expect(verifyBillingPayload(token, SECRET, { expectedUserId: 7 })).toBeNull();
    expect(verifyBillingPayload(token, SECRET, { expectedUserId: 42 })).not.toBeNull();
  });

  it("aplica la caducidad en pre-checkout y la ignora al asentar el pago", () => {
    // Esta asimetría es el punto: una renovación a los seis meses llega con el
    // payload original, así que exigir frescura al asentar rechazaría
    // precisamente a quien lleva más tiempo pagando.
    const old = Math.floor(Date.now() / 1000) - 90 * 24 * 3600;
    const token = signBillingPayload(
      { userId: 42, planCode: "premium_monthly", starsAmount: 250, issuedAt: old },
      SECRET);
    expect(verifyBillingPayload(token, SECRET, { maxAgeSeconds: 3600 })).toBeNull();
    expect(verifyBillingPayload(token, SECRET)).not.toBeNull();
  });

  it("cabe en el límite de 128 bytes de Telegram", () => {
    // Pasarse falla en `createInvoiceLink`, es decir frente al usuario y no en
    // revisión. Se prueba con el id más largo que Telegram puede emitir.
    const token = signBillingPayload(
      { userId: 9_999_999_999_999, planCode: "premium_monthly", starsAmount: 10_000 },
      SECRET);
    expect(Buffer.byteLength(token, "utf8")).toBeLessThanOrEqual(128);
  });

  it("rechaza entradas vacías o sin secreto", () => {
    expect(verifyBillingPayload(null, SECRET)).toBeNull();
    expect(verifyBillingPayload("", SECRET)).toBeNull();
    expect(verifyBillingPayload("v1.a.b.c", SECRET)).toBeNull();
  });
});
