import { eq } from "drizzle-orm";

import { database } from "@/lib/db";
import { sharedPredictionCards } from "@/lib/db/schema";
import { type ShareCard, isShareToken } from "@/lib/share-card";

/**
 * Lee una tarjeta compartida por su token. Sin sesión: es la ruta pública.
 *
 * El formato del token se valida antes de tocar la base para que una ruta
 * pública no convierta cualquier cadena de la URL en una consulta.
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
  return payload as unknown as ShareCard;
}
