import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  Object.assign(process.env, {
    TELEGRAM_BOT_TOKEN: "123456789:abcdefghijklmnopqrstuvwxyzABCDE",
    TELEGRAM_ACCESS_MODE: "private",
    TELEGRAM_ALLOWED_USER_IDS: "42",
    DIKAMAHA_BOT_API_URL: "https://api.example.test",
    DIKAMAHA_API_KEY: "1234567890abcdef",
    DATABASE_URL: "postgres://test:test@example.test:5432/test",
    MINIAPP_SESSION_SECRET: "0123456789abcdef0123456789abcdef",
  });
});
describe("signed Mini App sessions", () => {
  it("round-trips and rejects tampering", async () => {
    const { issueSession, parseSession, sessionCookieOptions, validCsrf } = await import("@/lib/auth/session");
    const issued = issueSession({ userId: 42, firstName: "Marco", username: "tester" });
    expect(parseSession(issued.token)).toMatchObject({ userId: 42, firstName: "Marco" });
    expect(parseSession(`${issued.token}x`)).toBeNull();
    expect(validCsrf(issued.session, issued.session.csrf)).toBe(true);
    expect(validCsrf(issued.session, "wrong")).toBe(false);
    expect(sessionCookieOptions()).toMatchObject({ httpOnly: true, sameSite: "lax" });
  });
});
