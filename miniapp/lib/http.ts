import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

import {
  type Entitlement,
  type Feature,
  requireFeature,
  resolveEntitlement,
} from "@/lib/auth/entitlements";
import { type Session, requireSession, validCsrf } from "@/lib/auth/session";
import { env } from "@/lib/env";

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

/**
 * Puerta única de las funciones de pago.
 *
 * Deja `authorizeRequest` haciendo exactamente lo que hacía -sesión, límite de
 * peticiones y CSRF- y añade encima la titularidad, de modo que las rutas
 * gratuitas siguen sin pagar ninguna consulta extra.
 */
export async function authorizeFeature(
  request: NextRequest,
  feature: Feature,
  mutation = false,
): Promise<{ session: Session; entitlement: Entitlement }> {
  const session = await authorizeRequest(request, mutation);
  const entitlement = await resolveEntitlement(session.userId);
  requireFeature(entitlement, feature);
  return { session, entitlement };
}

/**
 * Autentica una llamada entre servicios: bot o worker contra la Mini App.
 *
 * No hay sesión ni CSRF porque no hay navegador. La comparación es en tiempo
 * constante y tolera longitudes distintas sin lanzar: `timingSafeEqual` exige
 * buffers iguales, y dejar escapar ese throw convertiría una clave equivocada
 * en un 500 que además filtra información por el tiempo de respuesta.
 */
export function requireInternalKey(request: NextRequest): void {
  const supplied = request.headers.get("x-dikamaha-internal-key") ?? "";
  const expected = env().MINIAPP_INTERNAL_API_KEY;
  const a = Buffer.from(supplied);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) {
    throw new Error("internal_auth_rejected");
  }
}

export function authError(error: unknown): NextResponse {
  const code = error instanceof Error ? error.message : "request_failed";
  if (code === "authentication_required") return jsonError(code, 401);
  if (code === "csrf_rejected") return jsonError(code, 403);
  if (code === "internal_auth_rejected") return jsonError("unauthorized", 401);
  if (code === "admin_required") return jsonError(code, 403);
  // 402 y no 403: el cliente tiene que poder distinguir "no puedes" de
  // "todavía no has pagado", que son dos pantallas distintas. `client-api.ts`
  // ya propaga `payload.error` como mensaje, así que basta con el código.
  if (code === "premium_required") return jsonError(code, 402);
  if (code === "prediction_quota_exhausted") return jsonError(code, 402);
  if (code === "rate_limit_exceeded") {
    return NextResponse.json({ error: code }, {
      status: 429,
      headers: { "Retry-After": "60" },
    });
  }
  return jsonError("request_failed", 400);
}
