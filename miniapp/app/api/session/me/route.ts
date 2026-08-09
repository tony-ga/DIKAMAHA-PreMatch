import { NextResponse } from "next/server";

import { currentSession } from "@/lib/auth/session";
import { jsonError } from "@/lib/http";

export async function GET() {
  const session = await currentSession();
  if (!session) return jsonError("authentication_required", 401);
  return NextResponse.json({
    user: { id: session.userId, username: session.username, firstName: session.firstName },
    csrfToken: session.csrf,
  });
}
