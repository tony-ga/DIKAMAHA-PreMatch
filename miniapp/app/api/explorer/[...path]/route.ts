import { NextRequest } from "next/server";

import { jsonError } from "@/lib/http";
import { proxyGet } from "@/lib/proxy";
import { resolveExplorerPath } from "@/lib/explorer";

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  const { path } = await context.params;
  const upstream = resolveExplorerPath(path);
  if (!upstream) return jsonError("explorer_path_rejected", 404);
  return proxyGet(request, upstream);
}
