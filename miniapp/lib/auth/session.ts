import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

import { env } from "@/lib/env";

export const SESSION_COOKIE = "dikamaha_miniapp_session";
const SESSION_TTL_SECONDS = 12 * 60 * 60;

export type Session = {
  userId: number;
  username?: string;
  firstName: string;
  csrf: string;
  expiresAt: number;
};

function encode(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

function decode(value: string): string {
  return Buffer.from(value, "base64url").toString("utf8");
}

function signature(payload: string): string {
  return createHmac("sha256", env().MINIAPP_SESSION_SECRET)
    .update(payload)
    .digest("base64url");
}

export function issueSession(input: Omit<Session, "csrf" | "expiresAt">): {
  token: string;
  session: Session;
} {
  const session: Session = {
    ...input,
    csrf: randomBytes(24).toString("base64url"),
    expiresAt: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS,
  };
  const payload = encode(JSON.stringify(session));
  return { token: `${payload}.${signature(payload)}`, session };
}

export function parseSession(token: string | undefined): Session | null {
  if (!token) return null;
  const [payload, suppliedSignature, extra] = token.split(".");
  if (!payload || !suppliedSignature || extra) return null;
  const expected = Buffer.from(signature(payload));
  const supplied = Buffer.from(suppliedSignature);
  if (expected.length !== supplied.length || !timingSafeEqual(expected, supplied)) {
    return null;
  }
  try {
    const value = JSON.parse(decode(payload)) as Session;
    if (
      !Number.isSafeInteger(value.userId) || value.userId <= 0
      || typeof value.csrf !== "string"
      || value.expiresAt <= Math.floor(Date.now() / 1000)
    ) return null;
    return value;
  } catch {
    return null;
  }
}

export async function currentSession(): Promise<Session | null> {
  const store = await cookies();
  return parseSession(store.get(SESSION_COOKIE)?.value);
}

export async function requireSession(): Promise<Session> {
  const session = await currentSession();
  if (!session) throw new Error("authentication_required");
  return session;
}

export function sessionCookieOptions() {
  const production = process.env.NODE_ENV === "production";
  return {
    httpOnly: true,
    secure: production,
    sameSite: production ? "none" as const : "lax" as const,
    partitioned: production,
    path: "/",
    maxAge: SESSION_TTL_SECONDS,
  };
}

export function validCsrf(session: Session, supplied: string | null): boolean {
  if (!supplied) return false;
  const expected = Buffer.from(session.csrf);
  const candidate = Buffer.from(supplied);
  return expected.length === candidate.length && timingSafeEqual(expected, candidate);
}
