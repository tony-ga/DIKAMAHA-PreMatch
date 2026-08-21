import { NextRequest } from "next/server";
import { proxyPost } from "@/lib/proxy";

/**
 * Cotiza una combinación ya elegible.
 *
 * Idempotente: calcula una probabilidad conjunta y no muta ningún estado, así
 * que no consume cupo -a diferencia de `/api/predict/upcoming`, que sí cobra
 * una unidad porque produce una predicción nueva-. El gate de Fase 135 revalida
 * cada pierna aguas arriba; esta ruta no reimplementa esa comprobación.
 */
export async function POST(request: NextRequest) {
  return proxyPost(request, "/v1/parlay/quote", true, "high_probability");
}
