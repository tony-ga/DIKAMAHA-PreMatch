import { NextRequest, NextResponse } from "next/server";

import { userIsAuthorized } from "@/lib/auth/access";
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
    if (!userIsAuthorized(validated.user.id)) return jsonError("access_denied", 403);
    const user = validated.user;
    await database().insert(miniappUsers).values({
      telegramUserId: user.id,
      username: user.username,
      firstName: user.first_name.trim(),
      lastName: user.last_name,
      languageCode: user.language_code,
      lastSeenAt: new Date(),
    }).onConflictDoUpdate({
      target: miniappUsers.telegramUserId,
      set: {
        username: user.username,
        firstName: user.first_name.trim(),
        lastName: user.last_name,
        languageCode: user.language_code,
        lastSeenAt: new Date(),
      },
    });
    const issued = issueSession({
      userId: user.id,
      username: user.username,
      firstName: user.first_name.trim(),
    });
    const response = NextResponse.json({
      user: { id: user.id, username: user.username, firstName: user.first_name.trim() },
      csrfToken: issued.session.csrf,
      startParam: validated.startParam,
    });
    response.cookies.set(SESSION_COOKIE, issued.token, sessionCookieOptions());
    return response;
  } catch {
    return jsonError("telegram_authentication_failed", 401);
  }
}
