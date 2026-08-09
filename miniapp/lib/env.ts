import { z } from "zod";

const schema = z.object({
  TELEGRAM_BOT_TOKEN: z.string().min(20),
  TELEGRAM_ACCESS_MODE: z.enum(["private", "public"]).default("private"),
  TELEGRAM_ALLOWED_USER_IDS: z.string().default(""),
  DIKAMAHA_BOT_API_URL: z.string().url().refine((value) => value.startsWith("https://")),
  DIKAMAHA_API_KEY: z.string().min(16),
  DATABASE_URL: z.string().min(1),
  MINIAPP_SESSION_SECRET: z.string().min(32),
  MINIAPP_ENABLED: z.enum(["true", "false"]).default("false"),
  MINIAPP_ALERTS_ENABLED: z.enum(["true", "false"]).default("false"),
});

export type MiniappEnv = z.infer<typeof schema>;

let cached: MiniappEnv | undefined;

export function env(): MiniappEnv {
  cached ??= schema.parse(process.env);
  return cached;
}

export function allowedUserIds(value = env().TELEGRAM_ALLOWED_USER_IDS): Set<number> {
  const ids = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number);
  if (ids.some((id) => !Number.isSafeInteger(id) || id <= 0)) {
    throw new Error("telegram_allowed_user_ids_invalid");
  }
  return new Set(ids);
}
