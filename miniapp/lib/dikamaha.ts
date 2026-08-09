import { env } from "@/lib/env";

export class DikamahaError extends Error {
  constructor(public readonly status: number, message = "dikamaha_request_failed") {
    super(message);
  }
}

export async function dikamahaRequest(
  path: string,
  options: RequestInit = {},
): Promise<unknown> {
  if (!path.startsWith("/v1/")) throw new Error("dikamaha_path_rejected");
  const config = env();
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
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload || typeof payload !== "object") {
    throw new DikamahaError(response.status);
  }
  return payload;
}
