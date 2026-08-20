import { createHash, createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";

import { validateTelegramLogin } from "@/lib/auth/telegram-login";

const BOT_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE";
const NOW = 1_800_000_000;

/**
 * Firma un payload como lo hace el Telegram Login Widget.
 *
 * La diferencia con `initData` que estas pruebas existen para fijar: el secreto
 * es `SHA256(botToken)`, no `HMAC("WebAppData", botToken)`. Escribirlo aquí a
 * mano -y no reutilizando el código bajo prueba- es lo que hace que la prueba
 * detecte si alguien "unifica" los dos validadores por parecerse.
 */
function signedLogin(overrides: Record<string, string | number> = {}) {
  const payload: Record<string, string | number> = {
    id: 42,
    first_name: "Marco",
    username: "tester",
    auth_date: NOW,
    ...overrides,
  };
  const check = Object.entries(payload)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secret = createHash("sha256").update(BOT_TOKEN).digest();
  return {
    ...payload,
    hash: createHmac("sha256", secret).update(check).digest("hex"),
  };
}

describe("verificación del Telegram Login Widget", () => {
  it("acepta un payload auténtico y reciente", () => {
    expect(validateTelegramLogin(signedLogin(), BOT_TOKEN, NOW)).toMatchObject({
      user: { id: 42, first_name: "Marco", username: "tester" },
      authDate: NOW,
    });
  });

  it("entrega el mismo identificador que initData, que es lo que preserva la cuenta", () => {
    // La premisa entera de DEC-219: el usuario 42 del WebView y el usuario 42
    // del widget son la misma fila de `miniapp_users`.
    expect(validateTelegramLogin(signedLogin(), BOT_TOKEN, NOW).user.id).toBe(42);
  });

  it("rechaza un payload manipulado", () => {
    const payload = { ...signedLogin(), first_name: "Mallory" };
    expect(() => validateTelegramLogin(payload, BOT_TOKEN, NOW))
      .toThrow("telegram_login_signature_invalid");
  });

  it("rechaza un campo añadido después de firmar", () => {
    // Sin incluir todo el payload en el `data_check_string`, un atacante podría
    // colar campos extra bajo una firma legítima.
    const payload = { ...signedLogin(), id: 999 };
    expect(() => validateTelegramLogin(payload, BOT_TOKEN, NOW))
      .toThrow("telegram_login_signature_invalid");
  });

  it("rechaza la firma de initData sobre el mismo payload", () => {
    // El error clásico al implementar esto: usar el secreto del WebView.
    const payload: Record<string, string | number> = {
      id: 42, first_name: "Marco", username: "tester", auth_date: NOW,
    };
    const check = Object.entries(payload)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, value]) => `${key}=${value}`)
      .join("\n");
    const wrongSecret = createHmac("sha256", "WebAppData").update(BOT_TOKEN).digest();
    payload.hash = createHmac("sha256", wrongSecret).update(check).digest("hex");
    expect(() => validateTelegramLogin(payload, BOT_TOKEN, NOW))
      .toThrow("telegram_login_signature_invalid");
  });

  it("rechaza un payload caducado", () => {
    expect(() => validateTelegramLogin(signedLogin({ auth_date: NOW - 901 }), BOT_TOKEN, NOW))
      .toThrow("telegram_login_expired");
  });

  it("rechaza un payload fechado en el futuro", () => {
    expect(() => validateTelegramLogin(signedLogin({ auth_date: NOW + 600 }), BOT_TOKEN, NOW))
      .toThrow("telegram_login_auth_date_invalid");
  });

  it("rechaza un hash con forma incorrecta", () => {
    expect(() => validateTelegramLogin({ ...signedLogin(), hash: "abc" }, BOT_TOKEN, NOW))
      .toThrow("telegram_login_hash_missing");
  });

  it("rechaza un usuario sin nombre utilizable", () => {
    expect(() => validateTelegramLogin(signedLogin({ first_name: "   " }), BOT_TOKEN, NOW))
      .toThrow("telegram_login_user_invalid");
  });

  it("rechaza valores no escalares", () => {
    // Un objeto en el payload daría al atacante control sobre cómo se
    // serializa el `data_check_string`.
    const payload = { ...signedLogin(), extra: { nested: true } } as Record<string, unknown>;
    expect(() => validateTelegramLogin(payload, BOT_TOKEN, NOW))
      .toThrow("telegram_login_payload_invalid");
  });
});
