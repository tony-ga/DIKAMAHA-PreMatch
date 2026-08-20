import { NextResponse } from "next/server";

import { autoActivates, resolveAccess } from "@/lib/auth/access";
import { issueSession, SESSION_COOKIE, sessionCookieOptions } from "@/lib/auth/session";
import type { TelegramUser } from "@/lib/auth/telegram";
import { database } from "@/lib/db";
import { miniappUsers } from "@/lib/db/schema";
import { jsonError } from "@/lib/http";

/**
 * Alta y emisión de sesión, común a los dos caminos de entrada.
 *
 * Hay dos formas de demostrar quién eres -el `initData` del WebView y la firma
 * del Login Widget- pero **una sola cuenta**: el `id` que entregan ambas es el
 * mismo `telegram_user_id`, clave primaria de `miniapp_users` y clave foránea
 * de la suscripción, la cuota diaria, los favoritos y las alertas. Lo que
 * cambia entre contextos es exclusivamente cómo se valida la firma; todo lo que
 * viene después tiene que ser idéntico, y la única forma de garantizarlo es que
 * sea el mismo código y no dos copias que se parezcan hoy.
 */
export async function signInTelegramUser(
  user: TelegramUser,
  extra: Record<string, unknown> = {},
): Promise<NextResponse> {
  const firstName = user.first_name.trim();
  await database().insert(miniappUsers).values({
    telegramUserId: user.id,
    username: user.username,
    firstName,
    lastName: user.last_name,
    languageCode: user.language_code,
    lastSeenAt: new Date(),
    // Sólo aplica al alta inicial: en modo abierto no hay nada que aprobar y
    // dejar a todo el mundo en `pending` cerraría la aplicación entera.
    status: autoActivates() ? "active" : "pending",
    approvedAt: autoActivates() ? new Date() : null,
  }).onConflictDoUpdate({
    target: miniappUsers.telegramUserId,
    set: {
      username: user.username,
      firstName,
      lastName: user.last_name,
      languageCode: user.language_code,
      lastSeenAt: new Date(),
      // `status`, `role` y `plan` quedan deliberadamente fuera: los decide un
      // administrador y volver a escribirlos en cada inicio de sesión
      // devolvería a `pending` -o desbloquearía- a quien ya tiene un estado.
    },
  });
  // Después del alta, no antes: la decisión se lee de la fila, así que la
  // cuenta tiene que existir para poder evaluarla.
  const access = await resolveAccess(user.id);
  if (!access.granted) return jsonError(access.reason, 403);
  const issued = issueSession({
    userId: user.id,
    username: user.username,
    firstName,
    role: access.account.role,
    plan: access.account.plan,
  });
  const response = NextResponse.json({
    user: {
      id: user.id,
      username: user.username,
      firstName,
      role: access.account.role,
      plan: access.account.plan,
    },
    csrfToken: issued.session.csrf,
    ...extra,
  });
  response.cookies.set(SESSION_COOKIE, issued.token, sessionCookieOptions());
  return response;
}
