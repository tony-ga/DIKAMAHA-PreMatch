import { NextRequest } from "next/server";
import { proxyPost } from "@/lib/proxy";

export async function POST(request: NextRequest) {
  // Idempotente: calcula una predicción, no muta ningún estado. Ver el
  // comentario de `idempotent` en dikamahaRequest.
  return proxyPost(request, "/v1/predict/live/fixture", true);
}
