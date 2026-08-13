import { NextRequest, NextResponse } from "next/server";

import {
  currentSession,
  refreshedSessionToken,
  SESSION_COOKIE,
  sessionCookieOptions,
} from "@/lib/auth/session";
import { jsonError } from "@/lib/http";

export async function GET(request: NextRequest) {
  const session = await currentSession();
  if (!session) {
    // Instrumentación deliberada: la cookie es `SameSite=None; Partitioned`, y
    // en el WebView de Telegram esa partición puede no ser la misma entre una
    // apertura y la siguiente. Si lo es, cada reapertura rehace el alta
    // completa -validar `initData`, escribir en PostgreSQL, dos round-trips
    // más- antes de pedir el primer dato. Distinguir "no traía cookie" de
    // "traía una inválida o caducada" es lo que permite saber si el problema
    // es la partición o simplemente el vencimiento, sin cambiar el mecanismo a
    // ciegas.
    console.warn("[session] unauthenticated request", {
      cookiePresent: Boolean(request.cookies.get(SESSION_COOKIE)),
    });
    return jsonError("authentication_required", 401);
  }
  const response = NextResponse.json({
    user: {
      id: session.userId,
      username: session.username,
      firstName: session.firstName,
      // Cookies emitidas antes de las cuentas en base de datos no los traen;
      // se les aplica el valor por defecto hasta que caduquen.
      role: session.role ?? "user",
      plan: session.plan ?? "free",
    },
    csrfToken: session.csrf,
  });
  // Renovación rodante: quien usa la Mini App de vez en cuando no vuelve a
  // pasar nunca por el alta de Telegram.
  const renewed = refreshedSessionToken(session);
  if (renewed) response.cookies.set(SESSION_COOKIE, renewed, sessionCookieOptions());
  return response;
}
