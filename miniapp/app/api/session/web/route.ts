import { NextRequest } from "next/server";

import { signInTelegramUser } from "@/lib/auth/sign-in";
import { validateTelegramLogin } from "@/lib/auth/telegram-login";
import { env } from "@/lib/env";
import { jsonError } from "@/lib/http";

/**
 * Entrada desde el sitio web.
 *
 * Mismo destino que `/api/session/telegram` -misma cuenta, mismo `resolveAccess`,
 * misma cookie- cambiando **sólo** el validador de firma: aquí el pase lo emite
 * el Telegram Login Widget en lugar del WebView.
 *
 * Los errores colapsan en un único 401 igual que en la ruta hermana: distinguir
 * "firma inválida" de "payload caducado" no ayuda a nadie que esté iniciando
 * sesión de verdad y sí a quien está probando firmas.
 */
export async function POST(request: NextRequest) {
  if (env().MINIAPP_ENABLED !== "true") return jsonError("miniapp_disabled", 503);
  try {
    const body = await request.json() as Record<string, unknown>;
    const validated = validateTelegramLogin(body, env().TELEGRAM_BOT_TOKEN);
    return await signInTelegramUser(validated.user);
  } catch {
    return jsonError("telegram_authentication_failed", 401);
  }
}
