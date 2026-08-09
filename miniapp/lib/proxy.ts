import { NextRequest, NextResponse } from "next/server";

import { DikamahaError, dikamahaRequest } from "@/lib/dikamaha";
import { authError, authorizeRequest, jsonError } from "@/lib/http";

export async function proxyGet(request: NextRequest, path: string) {
  try {
    await authorizeRequest(request);
    const payload = await dikamahaRequest(`${path}${request.nextUrl.search}`);
    return NextResponse.json(payload);
  } catch (error) {
    if (error instanceof DikamahaError) {
      console.error("[bff] upstream GET unavailable", { path, status: error.status });
      return jsonError("upstream_unavailable", error.status === 429 ? 429 : error.status >= 500 ? 503 : 422);
    }
    return authError(error);
  }
}

export async function proxyPost(request: NextRequest, path: string) {
  try {
    await authorizeRequest(request, true);
    const body = await request.json();
    const payload = await dikamahaRequest(path, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(payload);
  } catch (error) {
    if (error instanceof DikamahaError) {
      console.error("[bff] upstream POST unavailable", { path, status: error.status });
      return jsonError("upstream_unavailable", error.status === 429 ? 429 : error.status >= 500 ? 503 : 422);
    }
    return authError(error);
  }
}
