import { createHmac, timingSafeEqual } from "node:crypto";

export type TelegramUser = {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
};

export type ValidatedTelegramData = {
  user: TelegramUser;
  authDate: number;
  queryId?: string;
  startParam?: string;
  chatType?: string;
};

const ALLOWED_CHAT_TYPES = new Set(["private", "sender"]);

export function validateTelegramInitData(
  initData: string,
  botToken: string,
  nowSeconds = Math.floor(Date.now() / 1000),
  maxAgeSeconds = 300,
): ValidatedTelegramData {
  if (!initData || initData.length > 8192) {
    throw new Error("telegram_init_data_invalid");
  }
  const params = new URLSearchParams(initData);
  const suppliedHash = params.get("hash") ?? "";
  if (!/^[a-f0-9]{64}$/i.test(suppliedHash)) {
    throw new Error("telegram_init_data_hash_missing");
  }
  params.delete("hash");
  const dataCheckString = [...params.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const expected = createHmac("sha256", secret).update(dataCheckString).digest();
  const supplied = Buffer.from(suppliedHash, "hex");
  if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
    throw new Error("telegram_init_data_signature_invalid");
  }

  const authDate = Number(params.get("auth_date"));
  if (!Number.isSafeInteger(authDate) || authDate > nowSeconds + 30) {
    throw new Error("telegram_auth_date_invalid");
  }
  if (nowSeconds - authDate > maxAgeSeconds) {
    throw new Error("telegram_init_data_expired");
  }
  const chatType = params.get("chat_type") ?? undefined;
  if (chatType && !ALLOWED_CHAT_TYPES.has(chatType)) {
    throw new Error("telegram_group_context_rejected");
  }

  let user: TelegramUser;
  try {
    user = JSON.parse(params.get("user") ?? "") as TelegramUser;
  } catch {
    throw new Error("telegram_user_invalid");
  }
  if (
    !Number.isSafeInteger(user.id) || user.id <= 0
    || typeof user.first_name !== "string" || !user.first_name.trim()
  ) {
    throw new Error("telegram_user_invalid");
  }
  return {
    user,
    authDate,
    queryId: params.get("query_id") ?? undefined,
    startParam: params.get("start_param") ?? undefined,
    chatType,
  };
}
