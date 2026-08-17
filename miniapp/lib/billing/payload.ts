import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

// Ruta relativa: este módulo también se carga desde `worker/billing-reconcile.ts`,
// que corre bajo `tsx` sin el alias `@/*`. `env` sólo se usa como valor por
// defecto de `secret` y el reconciliador siempre lo pasa explícito, así que
// nunca se invoca desde el worker -pero la importación en sí debe resolver.
import { env } from "../env";

/**
 * Payload firmado que viaja dentro de la factura de Telegram.
 *
 * Telegram nos devuelve este texto tal cual en `pre_checkout_query` y en cada
 * `successful_payment`, incluidas las renovaciones. Es el único dato que ata un
 * cobro a un usuario nuestro, así que va firmado: sin firma, cualquiera que
 * fabrique una factura con un `user_id` ajeno podría regalarse premium.
 *
 * Formato: `v1.<userId>.<planCode>.<stars>.<emitido>.<nonce>.<firma>`
 *
 * Telegram limita `invoice_payload` a 128 bytes. Con un id de 13 dígitos esto
 * ocupa ~100, y el test lo comprueba: pasarse falla en `createInvoiceLink`, es
 * decir frente al usuario y no en revisión.
 */
export type BillingPayload = {
  userId: number;
  planCode: string;
  starsAmount: number;
  issuedAt: number;
  nonce: string;
};

const VERSION = "v1";
const SEPARATOR = ".";

function sign(body: string, secret: string): string {
  return createHmac("sha256", secret).update(body).digest("base64url");
}

export function newNonce(): string {
  return randomBytes(12).toString("base64url");
}

export function signBillingPayload(
  input: Omit<BillingPayload, "issuedAt" | "nonce">
    & Partial<Pick<BillingPayload, "issuedAt" | "nonce">>,
  secret = env().MINIAPP_BILLING_SECRET,
): string {
  const issuedAt = input.issuedAt ?? Math.floor(Date.now() / 1000);
  const nonce = input.nonce ?? newNonce();
  const body = [
    VERSION, String(input.userId), input.planCode,
    String(input.starsAmount), String(issuedAt), nonce,
  ].join(SEPARATOR);
  return `${body}${SEPARATOR}${sign(body, secret)}`;
}

/**
 * Verifica firma y forma. `maxAgeSeconds` es opcional a propósito.
 *
 * En `pre_checkout_query` sí se acota la edad: una factura de hace días es
 * basura o un intento de repetición. En `successful_payment` **no** se puede
 * acotar, porque una renovación a los seis meses llega con el payload original;
 * exigir frescura ahí rechazaría precisamente a los suscriptores fieles.
 */
export function verifyBillingPayload(
  raw: string | null | undefined,
  secret = env().MINIAPP_BILLING_SECRET,
  options: { expectedUserId?: number; maxAgeSeconds?: number } = {},
): BillingPayload | null {
  if (typeof raw !== "string" || raw.length === 0 || raw.length > 256) return null;
  const parts = raw.split(SEPARATOR);
  if (parts.length !== 7) return null;
  const [version, rawUserId, planCode, rawStars, rawIssuedAt, nonce, supplied] = parts;
  if (version !== VERSION) return null;

  const body = parts.slice(0, 6).join(SEPARATOR);
  const expected = Buffer.from(sign(body, secret));
  const candidate = Buffer.from(supplied);
  // `timingSafeEqual` lanza si las longitudes difieren, así que se comparan
  // antes; devolver `null` y no propagar mantiene el fallo indistinguible.
  if (expected.length !== candidate.length) return null;
  if (!timingSafeEqual(expected, candidate)) return null;

  const userId = Number(rawUserId);
  const starsAmount = Number(rawStars);
  const issuedAt = Number(rawIssuedAt);
  if (!Number.isSafeInteger(userId) || userId <= 0) return null;
  if (!Number.isSafeInteger(starsAmount) || starsAmount <= 0) return null;
  if (!Number.isSafeInteger(issuedAt) || issuedAt <= 0) return null;
  if (!planCode || !nonce) return null;

  // El pagador tiene que ser el titular de la factura. Sin esta comprobación,
  // una factura válida reenviada a otra persona activaría la cuenta ajena.
  if (options.expectedUserId !== undefined && options.expectedUserId !== userId) {
    return null;
  }
  if (options.maxAgeSeconds !== undefined) {
    const age = Math.floor(Date.now() / 1000) - issuedAt;
    if (age > options.maxAgeSeconds || age < -60) return null;
  }
  return { userId, planCode, starsAmount, issuedAt, nonce };
}
