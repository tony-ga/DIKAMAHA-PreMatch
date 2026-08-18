import { eq } from "drizzle-orm";

import { database } from "@/lib/db";
import { sharedPredictionCards } from "@/lib/db/schema";
import { SHARE_CARD_VERSION, type ShareCard, isShareToken } from "@/lib/share-card";

/**
 * Lee una tarjeta compartida por su token. Sin sesión: es la ruta pública.
 *
 * El formato del token se valida antes de tocar la base para que una ruta
 * pública no convierta cualquier cadena de la URL en una consulta.
 *
 * Una tarjeta congelada con un formato anterior no se sirve. El renderizador
 * espera la forma vigente y pintarla con otra daría una imagen rota en vez de
 * un error; es preferible un 404 franco. La reparación existe: volver a
 * compartir ese partido reconstruye la fila conservando su token
 * (`POST /api/share`), así que el link vuelve a la vida en cuanto alguien lo
 * comparte de nuevo.
 */
export async function shareCardByToken(token: string): Promise<ShareCard | null> {
  if (!isShareToken(token)) return null;
  const [row] = await database()
    .select({ payload: sharedPredictionCards.payload })
    .from(sharedPredictionCards)
    .where(eq(sharedPredictionCards.token, token))
    .limit(1);
  const payload = row?.payload;
  if (!payload || typeof payload !== "object") return null;
  const card = payload as unknown as ShareCard;
  return card.version === SHARE_CARD_VERSION ? card : null;
}
