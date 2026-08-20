/**
 * Cuándo se puede vender Premium, y cuándo callar.
 *
 * Vive en `lib/` y no dentro del componente para poder probarse sin montar
 * React, igual que `lib/public-routes.ts`. Las reglas de esta función son la
 * parte que de verdad importa revisar: un banner que aparece cuando no debe es
 * un producto que pide dinero por algo que ya está dado, y uno que aparece
 * siempre deja de leerse en una semana.
 */

export type QuotaView = { used: number; limit: number; remaining: number };

export type SellableInput = {
  plan: "free" | "premium";
  enforced: boolean;
  quota: QuotaView | null;
} | undefined;

/**
 * La cuota del usuario **si hay algo que venderle**, o `null`.
 *
 * Tres motivos para callar, y los tres son correctos por razones distintas:
 *
 * - Sin datos todavía: pintar y despintar es peor que esperar.
 * - `enforced: false` -el cobro apagado-: nada está gateado, así que ofrecer
 *   Premium sería cobrar por lo que se está regalando.
 * - Ya es premium: no se le vende lo que ya paga.
 */
export function sellableQuota(data: SellableInput): QuotaView | null {
  if (!data || !data.enforced) return null;
  if (data.plan === "premium") return null;
  return data.quota ?? null;
}

/**
 * Si el aviso de cuota merece espacio.
 *
 * Sólo con una o ninguna restante. Con dos o tres por delante el límite todavía
 * no le estorba a nadie y el aviso sería ruido; el usuario aprende a ignorarlo
 * y deja de verlo justo el día que sí importa.
 */
export function quotaBannerVisible(quota: QuotaView | null): boolean {
  return quota !== null && quota.remaining <= 1;
}
