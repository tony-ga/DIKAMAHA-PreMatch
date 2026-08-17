import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

import { env } from "@/lib/env";

export const SESSION_COOKIE = "dikamaha_miniapp_session";
/**
 * Duración de la sesión: 30 días, no 12 horas.
 *
 * Con 12 horas, abrir la Mini App dos veces en un día ya obligaba a rehacer el
 * alta por Telegram -validar `initData`, escribir en PostgreSQL y dos
 * round-trips más antes de pedir el primer dato-. Nada en el modelo de amenaza
 * lo justificaba: la cookie es `HttpOnly`, va firmada con HMAC y su
 * autorización se revalida en cada emisión. `refreshSession` la renueva de
 * forma rodante, así que quien usa la aplicación no vuelve a autenticarse.
 */
const SESSION_TTL_SECONDS = 30 * 24 * 60 * 60;
/**
 * A partir de qué antigüedad conviene reemitir la cookie.
 *
 * Renovar en cada petición obligaría a escribir la cabecera `Set-Cookie` en
 * todas las respuestas; hacerlo sólo cuando ha pasado un día mantiene la
 * ventana efectiva en ~29 días sin ese coste.
 */
const SESSION_REFRESH_AFTER_SECONDS = 24 * 60 * 60;

export type Session = {
  userId: number;
  username?: string;
  firstName: string;
  csrf: string;
  expiresAt: number;
  /**
   * Rol y plan viajan dentro de la cookie firmada, no se consultan por
   * petición. `authorizeRequest` corre en cada llamada al proxy: leer la fila
   * ahí convertiría cada petición de catálogo en un viaje extra a PostgreSQL.
   * La cookie va firmada con HMAC, así que el cliente no puede alterarlos.
   *
   * **`plan` es sólo una pista para el primer pintado y no autoriza nada.**
   * Esta cookie dura 30 días y `refreshedSessionToken` la reemite sin releer la
   * cuenta, de modo que para una suscripción mensual sería obsoleta en los dos
   * sentidos: quien cancela conservaría el producto, y quien paga desde el bot
   * no lo tendría aquí hasta la siguiente emisión. La autoridad sobre el nivel
   * es `resolveEntitlement` (`lib/auth/entitlements.ts`). Invariante revisable
   * por grep: ninguna ruta puede autorizar leyendo `session.plan`.
   *
   * Opcionales porque las cookies emitidas antes de las cuentas en base de
   * datos no los traen y deben seguir siendo válidas hasta caducar.
   */
  role?: "user" | "admin";
  plan?: string;
};

function encode(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

function decode(value: string): string {
  return Buffer.from(value, "base64url").toString("utf8");
}

function signature(payload: string): string {
  return createHmac("sha256", env().MINIAPP_SESSION_SECRET)
    .update(payload)
    .digest("base64url");
}

export function issueSession(input: Omit<Session, "csrf" | "expiresAt">): {
  token: string;
  session: Session;
} {
  const session: Session = {
    ...input,
    csrf: randomBytes(24).toString("base64url"),
    expiresAt: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS,
  };
  const payload = encode(JSON.stringify(session));
  return { token: `${payload}.${signature(payload)}`, session };
}

export function parseSession(token: string | undefined): Session | null {
  if (!token) return null;
  const [payload, suppliedSignature, extra] = token.split(".");
  if (!payload || !suppliedSignature || extra) return null;
  const expected = Buffer.from(signature(payload));
  const supplied = Buffer.from(suppliedSignature);
  if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) {
    return null;
  }
  try {
    const value = JSON.parse(decode(payload)) as Session;
    if (
      !Number.isSafeInteger(value.userId) || value.userId <= 0
      || typeof value.csrf !== "string"
      || value.expiresAt <= Math.floor(Date.now() / 1000)
    ) return null;
    return value;
  } catch {
    return null;
  }
}

export async function currentSession(): Promise<Session | null> {
  const store = await cookies();
  return parseSession(store.get(SESSION_COOKIE)?.value);
}

export async function requireSession(): Promise<Session> {
  const session = await currentSession();
  if (!session) throw new Error("authentication_required");
  return session;
}

export function sessionCookieOptions() {
  const production = process.env.NODE_ENV === "production";
  return {
    httpOnly: true,
    secure: production,
    sameSite: production ? "none" as const : "lax" as const,
    partitioned: production,
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  };
}

/**
 * Reemite la cookie si ya lleva un día viva, conservando la misma sesión.
 *
 * Renovación rodante: mientras el usuario abra la Mini App de vez en cuando,
 * su sesión nunca caduca y no vuelve a pasar por el alta de Telegram. Conserva
 * el `csrf` original para no invalidar el token que el cliente ya tiene en
 * memoria.
 */
export function refreshedSessionToken(session: Session): string | null {
  const now = Math.floor(Date.now() / 1000);
  const remaining = session.expiresAt - now;
  // Una sesión ya vencida no se renueva, se rehace. Hoy esto es inalcanzable
  // -sólo se llama tras `currentSession()`, que ya rechaza lo caducado-, pero
  // sin la guarda esta función sabría resucitar sesiones muertas, y ese es
  // exactamente el tipo de primitiva que acaba llamándose desde otro sitio.
  if (remaining <= 0) return null;
  if (remaining > SESSION_TTL_SECONDS - SESSION_REFRESH_AFTER_SECONDS) {
    return null;
  }
  const renewed: Session = {
    ...session,
    expiresAt: now + SESSION_TTL_SECONDS,
  };
  const payload = encode(JSON.stringify(renewed));
  return `${payload}.${signature(payload)}`;
}

export function validCsrf(session: Session, supplied: string | null): boolean {
  if (!supplied) return false;
  const expected = Buffer.from(session.csrf);
  const candidate = Buffer.from(supplied);
  return expected.length === candidate.length && timingSafeEqual(expected, candidate);
}
