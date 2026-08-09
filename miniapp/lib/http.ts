import { NextRequest, NextResponse } from "next/server";

import { requireSession, validCsrf } from "@/lib/auth/session";

type Bucket = { startedAt: number; count: number };
const buckets = new Map<number, Bucket>();
const LIMIT = 60;
const WINDOW_MS = 60_000;

export function jsonError(code: string, status: number): NextResponse {
  return NextResponse.json({ error: code }, { status });
}

export async function authorizeRequest(
  request: NextRequest,
  mutation = false,
) {
  const session = await requireSession();
  const now = Date.now();
  const current = buckets.get(session.userId);
  const bucket = !current || now - current.startedAt >= WINDOW_MS
    ? { startedAt: now, count: 0 }
    : current;
  bucket.count += 1;
  buckets.set(session.userId, bucket);
  if (bucket.count > LIMIT) throw new Error("rate_limit_exceeded");
  if (mutation && !validCsrf(session, request.headers.get("x-csrf-token"))) {
    throw new Error("csrf_rejected");
  }
  return session;
}

export function authError(error: unknown): NextResponse {
  const code = error instanceof Error ? error.message : "request_failed";
  if (code === "authentication_required") return jsonError(code, 401);
  if (code === "csrf_rejected") return jsonError(code, 403);
  if (code === "rate_limit_exceeded") {
    return NextResponse.json({ error: code }, {
      status: 429,
      headers: { "Retry-After": "60" },
    });
  }
  return jsonError("request_failed", 400);
}
