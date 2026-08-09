import { NextRequest, NextResponse } from "next/server";

import { env } from "@/lib/env";
import { authError, authorizeRequest } from "@/lib/http";

export async function GET(request: NextRequest) {
  try {
    await authorizeRequest(request);
    const source = request.nextUrl.searchParams.get("url") ?? "";
    if (!source) return NextResponse.json({ error: "media_url_required" }, { status: 422 });
    const config = env();
    const upstream = new URL("/v1/media/image", config.DIKAMAHA_BOT_API_URL);
    upstream.searchParams.set("url", source);
    const response = await fetch(upstream, {
      headers: {
        "X-Dikamaha-Key": config.DIKAMAHA_API_KEY,
        "X-Request-ID": `miniapp-media-${crypto.randomUUID()}`,
      },
      signal: AbortSignal.timeout(12_000),
    });
    if (!response.ok || response.headers.get("content-type")?.split(";")[0] !== "image/png") {
      return NextResponse.json({ error: "media_unavailable" }, { status: 422 });
    }
    return new NextResponse(await response.arrayBuffer(), {
      headers: {
        "Content-Type": "image/png",
        "Cache-Control": "private, max-age=86400, immutable",
        "X-Content-Type-Options": "nosniff",
      },
    });
  } catch (error) {
    return authError(error);
  }
}
