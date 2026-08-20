import { createHash, createHmac, timingSafeEqual } from "node:crypto";

import type { TelegramUser } from "@/lib/auth/telegram";

export type ValidatedLoginData = {
  user: TelegramUser;
  authDate: number;
  photoUrl?: string;
};

/**
 * Payload del Telegram Login Widget: campos planos, no el `initData` del
 * WebView.
 */
export type TelegramLoginPayload = Record<string, unknown>;

/**
 * Ventana de validez del payload del widget.
 *
 * Más ancha que los 300 s de `initData` a propósito: allí el WebView entrega
 * los datos ya listos, aquí media un humano que pulsa el botón, confirma en la
 * aplicación de Telegram -a veces en otro dispositivo- y vuelve. 300 s dejaría
 * fuera un inicio de sesión perfectamente legítimo. Sigue siendo una ventana
 * corta: el payload no es una credencial reutilizable, sólo el pase para emitir
 * la sesión propia.
 */
const MAX_AGE_SECONDS = 900;

/**
 * Valida la firma del Telegram Login Widget.
 *
 * Hermano de `validateTelegramInitData`, con una diferencia de formato que hay
 * que respetar y que es la fuente habitual de errores: el widget firma con
 * `secret = SHA256(botToken)`, mientras que `initData` usa
 * `secret = HMAC("WebAppData", botToken)`. El `data_check_string` se construye
 * igual -claves ordenadas, `clave=valor`, separadas por `\n`-, y todas las
 * demás defensas se heredan tal cual: comparación en tiempo constante, forma
 * del hash, y `auth_date` ni futuro ni caducado.
 *
 * Devuelve el mismo `TelegramUser` que la ruta del WebView, porque es
 * literalmente el mismo usuario: el `id` que entrega el widget es el
 * `telegram_user_id` que ya es clave primaria de `miniapp_users`.
 */
export function validateTelegramLogin(
  payload: TelegramLoginPayload,
  botToken: string,
  nowSeconds = Math.floor(Date.now() / 1000),
  maxAgeSeconds = MAX_AGE_SECONDS,
): ValidatedLoginData {
  if (!payload || typeof payload !== "object") {
    throw new Error("telegram_login_payload_invalid");
  }
  const suppliedHash = typeof payload.hash === "string" ? payload.hash : "";
  if (!/^[a-f0-9]{64}$/i.test(suppliedHash)) {
    throw new Error("telegram_login_hash_missing");
  }

  const entries: [string, string][] = [];
  for (const [key, value] of Object.entries(payload)) {
    if (key === "hash") continue;
    // El widget sólo emite escalares. Cualquier otra cosa es un payload
    // fabricado, y serializarla para firmarla sólo daría al atacante control
    // sobre el `data_check_string`.
    if (value === null || value === undefined) continue;
    if (typeof value === "object") throw new Error("telegram_login_payload_invalid");
    entries.push([key, String(value)]);
  }
  if (entries.length > 32) throw new Error("telegram_login_payload_invalid");

  const dataCheckString = entries
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secret = createHash("sha256").update(botToken).digest();
  const expected = createHmac("sha256", secret).update(dataCheckString).digest();
  const supplied = Buffer.from(suppliedHash, "hex");
  if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
    throw new Error("telegram_login_signature_invalid");
  }

  const authDate = Number(payload.auth_date);
  if (!Number.isSafeInteger(authDate) || authDate > nowSeconds + 30) {
    throw new Error("telegram_login_auth_date_invalid");
  }
  if (nowSeconds - authDate > maxAgeSeconds) {
    throw new Error("telegram_login_expired");
  }

  const id = Number(payload.id);
  const firstName = typeof payload.first_name === "string" ? payload.first_name : "";
  if (!Number.isSafeInteger(id) || id <= 0 || !firstName.trim()) {
    throw new Error("telegram_login_user_invalid");
  }
  return {
    user: {
      id,
      first_name: firstName,
      last_name: typeof payload.last_name === "string" ? payload.last_name : undefined,
      username: typeof payload.username === "string" ? payload.username : undefined,
      language_code: undefined,
    },
    authDate,
    photoUrl: typeof payload.photo_url === "string" ? payload.photo_url : undefined,
  };
}
