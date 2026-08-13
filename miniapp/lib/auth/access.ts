import { eq } from "drizzle-orm";

import { database } from "@/lib/db";
import {
  type AccountRole,
  type AccountStatus,
  miniappUsers,
} from "@/lib/db/schema";
import { allowedUserIds, env } from "@/lib/env";

export type Account = {
  status: AccountStatus;
  role: AccountRole;
  plan: string;
};

/** Resultado de evaluar el acceso: o entra, o hay un motivo publicable. */
export type AccessDecision =
  | { granted: true; account: Account }
  | { granted: false; reason: "access_pending" | "access_blocked" };

/**
 * Decide el acceso leyendo la cuenta, no la configuración del proceso.
 *
 * Antes esto era una comprobación contra `TELEGRAM_ALLOWED_USER_IDS`, de modo
 * que **cada usuario nuevo exigía un redespliegue**. Ahora el alta es un
 * UPDATE sobre `miniapp_users` y la variable de entorno queda sólo como
 * semilla de administradores: quien está en ella entra siempre, para que un
 * error de datos no pueda dejar el sistema sin nadie que pueda aprobar a
 * nadie.
 *
 * Se llama justo después de dar de alta la fila, así que la cuenta siempre
 * existe cuando llega aquí.
 */
export async function resolveAccess(userId: number): Promise<AccessDecision> {
  const config = env();
  if (allowedUserIds(config.TELEGRAM_ALLOWED_USER_IDS).has(userId)) {
    return { granted: true, account: await promoteToAdmin(userId) };
  }
  const [row] = await database()
    .select({
      status: miniappUsers.status,
      role: miniappUsers.role,
      plan: miniappUsers.plan,
    })
    .from(miniappUsers)
    .where(eq(miniappUsers.telegramUserId, userId))
    .limit(1);
  const account: Account = {
    status: row?.status ?? "pending",
    role: row?.role ?? "user",
    plan: row?.plan ?? "free",
  };
  if (account.status === "active") return { granted: true, account };
  // `blocked` y `pending` se distinguen a propósito: al primero se le negó el
  // acceso, el segundo sólo está esperando. Colapsarlos en un único
  // "acceso denegado" deja al usuario sin saber si debe pedir el alta o dejar
  // de intentarlo.
  return {
    granted: false,
    reason: account.status === "blocked" ? "access_blocked" : "access_pending",
  };
}

/**
 * Activa y marca como administrador a quien viene en la semilla de entorno.
 *
 * Escribe en vez de sólo devolver el veredicto para que la lista blanca quede
 * reflejada en la tabla: así el panel de administración muestra el estado real
 * y no una autorización invisible que sólo existe en la configuración.
 */
async function promoteToAdmin(userId: number): Promise<Account> {
  const account: Account = { status: "active", role: "admin", plan: "free" };
  const [row] = await database()
    .update(miniappUsers)
    .set({ status: "active", role: "admin", approvedAt: new Date() })
    .where(eq(miniappUsers.telegramUserId, userId))
    .returning({ plan: miniappUsers.plan });
  return row ? { ...account, plan: row.plan } : account;
}

/**
 * Aplica el modo de acceso abierto sobre un alta recién creada.
 *
 * Con `TELEGRAM_ACCESS_MODE=public` no hay nada que aprobar y dejar a todo el
 * mundo en `pending` cerraría la aplicación por completo.
 */
export function autoActivates(): boolean {
  return env().TELEGRAM_ACCESS_MODE === "public";
}
