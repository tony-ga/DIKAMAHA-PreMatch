import { ImageResponse } from "next/og";

import { SHARE_IMAGE_SIZE, ShareCardImage } from "@/lib/share-card-image";
import { shareCardByToken } from "@/lib/share-store";

/**
 * PNG público de una tarjeta pre-match congelada.
 *
 * Ruta pública a propósito: es la imagen que viaja por WhatsApp y Telegram,
 * tanto incrustada en la vista previa del link como abierta directamente. Lo
 * único que la protege es la entropía del token (256 bits, `shareToken`).
 *
 * Se sirve desde la fila ya congelada, sin llamar al backend: una tarjeta que
 * circula puede recibir muchas vistas previas seguidas y ninguna debe costar
 * una predicción.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ token: string }> },
) {
  const { token } = await params;
  const card = await shareCardByToken(token);
  if (!card) return new Response("not_found", { status: 404 });
  return new ImageResponse(<ShareCardImage card={card} />, {
    ...SHARE_IMAGE_SIZE,
    headers: {
      // La tarjeta es inmutable por diseño, así que una vista previa cacheada
      // nunca puede quedar desactualizada.
      "Cache-Control": "public, max-age=31536000, immutable",
      "Content-Type": "image/png",
    },
  });
}
