import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/auth/session";
import { authError, authorizeRequest } from "@/lib/http";

export async function POST(request: NextRequest) {
  try {
    await authorizeRequest(request, true);
    const response = NextResponse.json({ status: "logged_out" });
    response.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions(), maxAge: 0 });
    return response;
  } catch (error) {
    return authError(error);
  }
}
