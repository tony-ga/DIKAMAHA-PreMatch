import { env } from "@/lib/env";

/**
 * Descarga un escudo y lo devuelve como data URI, para incrustarlo al congelar.
 *
 * Se incrusta en vez de guardar la URL por la misma razon por la que la tarjeta
 * guarda la prediccion ya resuelta: servir la imagen no debe depender de nadie
 * mas. Con la URL, cada vista previa de WhatsApp haria que el servidor saliera
 * a buscar el escudo a ESPN, y un link que circula recibe muchas seguidas.
 *
 * Va por `/v1/media/image`, el proxy que ya usa `/api/media`, no por `fetch`
 * directo a la URL que manda el cliente. Ese proxy es quien valida
 * (`fetch_transparent_png`, `src/provider_media.py`): exige HTTPS, comprueba el
 * host contra una lista permitida, corta por tamano y verifica la firma PNG.
 * Salir directo desde la Mini App convertiria un campo del cuerpo de la
 * peticion en una URL arbitraria que el servidor visita.
 */

/** Tope de lo que se acepta incrustar en la fila del partido. */
const MAX_LOGO_BYTES = 120_000;

export async function shareLogoDataUri(source: string): Promise<string> {
  if (!source.startsWith("https://")) return "";
  try {
    const config = env();
    const upstream = new URL("/v1/media/image", config.DIKAMAHA_BOT_API_URL);
    upstream.searchParams.set("url", source);
    const response = await fetch(upstream, {
      headers: {
        "X-Dikamaha-Key": config.DIKAMAHA_API_KEY,
        "X-Request-ID": `miniapp-share-logo-${crypto.randomUUID()}`,
      },
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return "";
    if (response.headers.get("content-type")?.split(";")[0] !== "image/png") {
      return "";
    }
    const bytes = await response.arrayBuffer();
    if (bytes.byteLength > MAX_LOGO_BYTES) return "";
    return `data:image/png;base64,${Buffer.from(bytes).toString("base64")}`;
  } catch {
    // Un escudo ausente degrada al monograma de iniciales que ya pinta
    // `Crest`; nunca debe impedir que la tarjeta se congele.
    return "";
  }
}
