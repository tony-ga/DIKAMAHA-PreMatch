import { NextRequest } from "next/server";

import { signInTelegramUser } from "@/lib/auth/sign-in";
import { validateTelegramInitData } from "@/lib/auth/telegram";
import { env } from "@/lib/env";
import { jsonError } from "@/lib/http";

export async function POST(request: NextRequest) {
  if (env().MINIAPP_ENABLED !== "true") return jsonError("miniapp_disabled", 503);
  try {
    const body = await request.json() as { initData?: unknown };
    if (typeof body.initData !== "string") return jsonError("telegram_init_data_missing", 400);
    const validated = validateTelegramInitData(body.initData, env().TELEGRAM_BOT_TOKEN);
    return await signInTelegramUser(validated.user, { startParam: validated.startParam });
  } catch {
    return jsonError("telegram_authentication_failed", 401);
  }
}
