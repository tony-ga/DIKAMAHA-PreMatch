import { allowedUserIds, env } from "@/lib/env";

export function userIsAuthorized(userId: number): boolean {
  const config = env();
  if (config.TELEGRAM_ACCESS_MODE === "public") return true;
  return allowedUserIds(config.TELEGRAM_ALLOWED_USER_IDS).has(userId);
}
