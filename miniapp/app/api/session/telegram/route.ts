import { NextRequest, NextResponse } from "next/server";

import { autoActivates, resolveAccess } from "@/lib/auth/access";
import { issueSession, SESSION_COOKIE, sessionCookieOptions } from "@/lib/auth/session";
import { validateTelegramInitData } from "@/lib/auth/telegram";
import { database } from "@/lib/db";
import { miniappUsers } from "@/lib/db/schema";
import { env } from "@/lib/env";
import { jsonError } from "@/lib/http";

export async function POST(request: NextRequest) {
  if (env().MINIAPP_ENABLED !== "true") return jsonError("miniapp_disabled", 503);
  try {
    const body = await request.json() as { initData?: unknown };
    if (typeof body.initData !== "string") return jsonError("telegram_init_data_missing", 400);
    const validated = validateTelegramInitData(body.initData, env().TELEGRAM_BOT_TOKEN);
    const user = validated.user;
    await database().insert(miniappUsers).values({
      telegramUserId: user.id,
      username: user.username,
      firstName: user.first_name.trim(),
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
        firstName: user.first_name.trim(),
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
      firstName: user.first_name.trim(),
      role: access.account.role,
      plan: access.account.plan,
    });
    const response = NextResponse.json({
      user: {
        id: user.id,
        username: user.username,
        firstName: user.first_name.trim(),
        role: access.account.role,
        plan: access.account.plan,
      },
      csrfToken: issued.session.csrf,
      startParam: validated.startParam,
    });
    response.cookies.set(SESSION_COOKIE, issued.token, sessionCookieOptions());
    return response;
  } catch {
    return jsonError("telegram_authentication_failed", 401);
  }
}
