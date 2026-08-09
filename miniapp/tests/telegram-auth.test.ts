import { createHmac } from "node:crypto";
import { describe, expect, it } from "vitest";

import { validateTelegramInitData } from "@/lib/auth/telegram";

const BOT_TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE";
const NOW = 1_800_000_000;

function signedInitData(overrides: Record<string, string> = {}) {
  const params = new URLSearchParams({
    auth_date: String(NOW),
    query_id: "AAE-test",
    user: JSON.stringify({ id: 42, first_name: "Marco", username: "tester" }),
    ...overrides,
  });
  const check = [...params.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secret = createHmac("sha256", "WebAppData").update(BOT_TOKEN).digest();
  params.set("hash", createHmac("sha256", secret).update(check).digest("hex"));
  return params.toString();
}

describe("Telegram initData verification", () => {
  it("accepts authentic and recent private data", () => {
    expect(validateTelegramInitData(signedInitData(), BOT_TOKEN, NOW)).toMatchObject({
      user: { id: 42, first_name: "Marco" },
      authDate: NOW,
    });
  });

  it("rejects a modified signature", () => {
    const value = signedInitData().replace("Marco", "Mallory");
    expect(() => validateTelegramInitData(value, BOT_TOKEN, NOW)).toThrow("signature_invalid");
  });

  it("rejects expired data", () => {
    expect(() => validateTelegramInitData(signedInitData({ auth_date: String(NOW - 301) }), BOT_TOKEN, NOW))
      .toThrow("expired");
  });

  it("rejects group launches", () => {
    expect(() => validateTelegramInitData(signedInitData({ chat_type: "group" }), BOT_TOKEN, NOW))
      .toThrow("group_context_rejected");
  });
});
