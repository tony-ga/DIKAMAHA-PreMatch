import { sql } from "drizzle-orm";

import { database } from "@/lib/db";
import type { AccountPlan, AccountRole, PlanSource } from "@/lib/db/schema";
import { billingEnabled } from "@/lib/env";

/**
 * Funciones que el nivel de pago abre. Nombrarlas -en vez de comparar
 * `plan === "premium"` en cada ruta- es lo que permite auditar de un vistazo
 * qué hay detrás del muro y mover una función de nivel sin buscar comparaciones
 * sueltas por todo el árbol.
 */
export type Feature =
  | "live"
  | "high_probability"
  | "unlimited_predictions"
  | "unlimited_favorites"
  | "unlimited_alerts";

export type Entitlement = {
  userId: number;
  plan: AccountPlan;
  role: AccountRole;
  planSource: PlanSource;
  expiresAt: Date | null;
  /**
   * `false` cuando el cobro está apagado: nada se gatea y nada se cobra.
   * La UI lo usa para no enseñar un muro que no existe.
   */
  enforced: boolean;
};

type CacheEntry = { value: Entitlement; expiresAtMs: number };

const cache = new Map<number, CacheEntry>();
/**
 * 60 s es el retraso máximo entre pagar y verlo reflejado, y `invalidate`
 * elimina incluso ése en el camino de pago. La Mini App corre con una sola
 * réplica (`railway.miniapp.toml`), así que una caché de proceso es
 * prácticamente una caché global.
 */
const TTL_MS = 60_000;
/** Tope de entradas, con la misma disciplina acotada que el rate limiter. */
const MAX_ENTRIES = 5_000;

function premiumFallback(userId: number, enforced: boolean): Entitlement {
  return {
    userId,
    plan: "premium",
    role: "user",
    planSource: "default",
    expiresAt: null,
    enforced,
  };
}

/**
 * Resuelve el nivel real de un usuario leyendo la fila, no la cookie.
 *
 * La cookie de sesión dura 30 días y `refreshedSessionToken` copia sus campos
 * sin releer la cuenta, así que el `plan` que viaja en ella puede estar hasta
 * un mes obsoleto. Para una suscripción mensual eso significaría dos fallos
 * simétricos: quien cancela conservaría el producto, y quien paga desde el bot
 * a las tres de la mañana no lo tendría en la Mini App hasta que la cookie se
 * reemitiera. Por eso la autoridad es esta función y `session.plan` queda como
 * simple pista para el primer pintado.
 *
 * La lectura es por clave primaria contra una tabla de pocos cientos de filas,
 * y sólo ocurre en rutas de pago: el catálogo, el historial y el explorador
 * siguen sin tocar la base, que era justo lo que protegía el diseño original de
 * la sesión.
 */
export async function resolveEntitlement(userId: number): Promise<Entitlement> {
  // Antes de cualquier consulta: con el cobro apagado no hay nada que resolver.
  if (!billingEnabled()) return premiumFallback(userId, false);

  const now = Date.now();
  const cached = cache.get(userId);
  if (cached && cached.expiresAtMs > now) return cached.value;

  let value: Entitlement;
  try {
    // La caducidad se computa aquí, espejo de `effective_plan` en SQL. Un
    // premium vencido lee 'free' en la petición siguiente sin depender de
    // ningún barrido: un barrido que no corra dejaría premium a quien no paga.
    const rows = await database().execute<{
      role: AccountRole;
      plan_source: PlanSource;
      plan_expires_at: string | Date | null;
      effective_plan: AccountPlan;
    }>(sql`
      SELECT "role", "plan_source", "plan_expires_at",
             CASE
               WHEN "plan" = 'premium'
                AND ("plan_source" = 'admin' OR "plan_expires_at" > now())
               THEN 'premium' ELSE 'free'
             END AS "effective_plan"
        FROM "miniapp_users"
       WHERE "telegram_user_id" = ${userId}
       LIMIT 1
    `);
    const row = rows[0];
    const expiresAt = row?.plan_expires_at ? new Date(row.plan_expires_at) : null;
    // Un administrador es premium por definición, igual que `promoteToAdmin`
    // lo activa por definición: quien administra el servicio no puede quedarse
    // fuera de él por no haber pagado.
    const isAdmin = row?.role === "admin";
    value = {
      userId,
      plan: isAdmin ? "premium" : (row?.effective_plan ?? "free"),
      role: row?.role ?? "user",
      planSource: row?.plan_source ?? "default",
      expiresAt: isAdmin ? null : expiresAt,
      enforced: true,
    };
  } catch (error) {
    // Fallo cerrado hacia `free`, que no es un cierre: el usuario conserva
    // historial, catálogo y sus predicciones diarias. Devolver premium ante un
    // error regalaría el producto en cada parpadeo de la base.
    console.error(JSON.stringify({
      event: "entitlement_resolution_failed",
      userId,
      error: error instanceof Error ? error.message : "unknown",
    }));
    return {
      userId, plan: "free", role: "user", planSource: "default",
      expiresAt: null, enforced: true,
    };
    // Deliberadamente sin cachear: un fallo transitorio no debe fijar 60
    // segundos de degradación para alguien que sí pagó.
  }

  if (cache.size >= MAX_ENTRIES) {
    const oldest = cache.keys().next();
    if (!oldest.done) cache.delete(oldest.value);
  }
  cache.set(userId, { value, expiresAtMs: now + TTL_MS });
  return value;
}

const PREMIUM_ONLY: ReadonlySet<Feature> = new Set<Feature>([
  "live",
  "high_probability",
  "unlimited_predictions",
  "unlimited_favorites",
  "unlimited_alerts",
]);

export function allows(entitlement: Entitlement, feature: Feature): boolean {
  if (!entitlement.enforced) return true;
  if (!PREMIUM_ONLY.has(feature)) return true;
  return entitlement.plan === "premium";
}

/** Lanza `premium_required`, que `authError` traduce a 402. */
export function requireFeature(entitlement: Entitlement, feature: Feature): void {
  if (!allows(entitlement, feature)) throw new Error("premium_required");
}

/**
 * Borra la entrada cacheada de un usuario.
 *
 * La llaman el alta de pago, el reembolso y la degradación, para que activar
 * una suscripción se note de inmediato en lugar de tras el TTL. Es lo que
 * convierte los 60 s en un techo teórico y no en la espera habitual.
 */
export function invalidateEntitlement(userId: number): void {
  cache.delete(userId);
}

/** Sólo para pruebas: deja la caché en un estado conocido. */
export function resetEntitlementCache(): void {
  cache.clear();
}
