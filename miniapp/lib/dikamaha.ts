import { env } from "@/lib/env";

export class DikamahaError extends Error {
  constructor(
    public readonly status: number,
    message = "dikamaha_request_failed",
    // Código de contrato que devuelve DIKAMAHA en un 422, por ejemplo
    // `league_history_below_minimum`. Sin él la interfaz sólo puede decir que
    // algo falló, y el usuario no distingue "no hay historial de esta
    // competición" de "el servicio está caído".
    public readonly reason: string | null = null,
  ) {
    super(message);
  }
}

/** Extrae el código de contrato de un error DIKAMAHA sin exponer el cuerpo. */
function upstreamReason(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const detail = (payload as Record<string, unknown>).detail;
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object") return null;
  const code = (detail as Record<string, unknown>).code;
  const message = (detail as Record<string, unknown>).message;
  const value = typeof code === "string" && code ? code : message;
  return typeof value === "string" && value ? value : null;
}

export async function dikamahaRequest(
  path: string,
  options: RequestInit = {},
): Promise<unknown> {
  if (!path.startsWith("/v1/")) throw new Error("dikamaha_path_rejected");
  const config = env();
  const method = String(options.method ?? "GET").toUpperCase();
  const attempts = method === "GET" ? 3 : 1;
  let lastStatus = 503;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(`${config.DIKAMAHA_BOT_API_URL.replace(/\/$/, "")}${path}`, {
        ...options,
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-Dikamaha-Key": config.DIKAMAHA_API_KEY,
          "X-Request-ID": `miniapp-${crypto.randomUUID()}`,
          ...options.headers,
        },
        signal: AbortSignal.timeout(35_000),
      });
      lastStatus = response.status;
      const payload = await response.json().catch(() => null);
      if (response.ok && payload && typeof payload === "object") return payload;
      if (response.status < 500 && response.status !== 429) {
        throw new DikamahaError(
          response.status, "dikamaha_request_failed", upstreamReason(payload));
      }
    } catch (error) {
      if (error instanceof DikamahaError) throw error;
      lastStatus = 503;
    }
    console.warn("[dikamaha] transient request failure", { path, attempt, status: lastStatus });
    if (attempt < attempts) {
      await new Promise((resolve) => setTimeout(resolve, attempt * 150));
    }
  }
  throw new DikamahaError(lastStatus);
}
