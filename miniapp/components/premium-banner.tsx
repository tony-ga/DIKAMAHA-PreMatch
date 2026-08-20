"use client";

import Link from "next/link";

import { SubscribeButton, useEntitlement } from "@/components/premium-gate";
import { quotaBannerVisible, sellableQuota } from "@/lib/premium-banner";

/**
 * Superficies comerciales **fuera del muro**.
 *
 * Hasta ahora vender sólo ocurría al chocar: `PremiumUpsell` aparece cuando el
 * usuario ya intentó abrir algo bloqueado. Eso deja sin cubrir los dos momentos
 * que de verdad importan -cuando se le acaba la cuota del día, y cuando acaba
 * de recibir valor- y obliga a descubrir el límite estrellándose contra él.
 *
 * Tres reglas gobiernan todo lo de este archivo:
 *
 * 1. **Se vende acceso y volumen, nunca retorno.** El proyecto tiene congelados
 *    ROI, Kelly, stakes y cuotas, y ésta es exactamente la superficie donde más
 *    tienta saltarse esa restricción. Ni una palabra sobre ganancias, aciertos
 *    garantizados o rentabilidad.
 * 2. **Ninguna urgencia fabricada.** Sin cuentas atrás, sin "oferta que
 *    termina", sin escasez inventada. Las únicas cifras que se muestran son
 *    reales y ya están en pantalla: las predicciones que le quedan hoy y los
 *    partidos que hay en vivo ahora mismo.
 * 3. **Nada que no se pueda cumplir.** Si la cuota vuelve mañana, se dice: es
 *    verdad y además evita que alguien pague creyendo que ha perdido el acceso
 *    para siempre.
 */

/** Si hay algo que vender. Las reglas viven en `lib/premium-banner.ts`. */
function useQuotaState() {
  const { data } = useEntitlement();
  return sellableQuota(data);
}

/**
 * Aviso de cuota, en el sitio donde el límite se nota.
 *
 * Sólo aparece cuando queda una o ninguna: con dos o tres por delante el aviso
 * sería ruido, y un banner que sale siempre deja de leerse a la semana.
 */
export function QuotaBanner({ compact = false }: { compact?: boolean }) {
  const quota = useQuotaState();
  if (!quotaBannerVisible(quota) || !quota) return null;

  const exhausted = quota.remaining <= 0;
  return (
    <aside className={exhausted ? "premium-banner spent" : "premium-banner"}>
      <div className="premium-banner-copy">
        <p className="eyebrow">{exhausted ? "SIN PREDICCIONES HOY" : "TE QUEDA 1 HOY"}</p>
        <strong>
          {exhausted
            ? `Has usado tus ${quota.limit} predicciones de hoy`
            : `Vas por ${quota.used} de ${quota.limit} predicciones`}
        </strong>
        {/* Decirlo evita que alguien pague creyendo que perdió el acceso. Que
            la frase juegue en contra de la venta es justamente por qué se
            queda. */}
        <span className="muted">
          {exhausted
            ? "Se renuevan solas cada día. Con Premium no hay límite."
            : "Con Premium no hay límite diario."}
        </span>
      </div>
      {compact
        ? <Link className="secondary-button" href="/settings">Ver Premium</Link>
        : <SubscribeButton label="Quitar el límite" />}
    </aside>
  );
}

/**
 * Qué hay detrás del muro, con cifras reales de esta pantalla.
 *
 * `liveCount` es el número de partidos en vivo que el usuario **ya está
 * viendo** en el listado: no es escasez fabricada, es lo que hay ahora y no
 * puede abrir. Con cero partidos vivos no se pinta, porque entonces la frase
 * sería falsa.
 */
export function PremiumHighlight({ liveCount }: { liveCount?: number }) {
  const quota = useQuotaState();
  if (!quota) return null;

  const live = Number(liveCount ?? 0);
  return (
    <aside className="premium-banner highlight">
      <div className="premium-banner-copy">
        <p className="eyebrow">DIKAMAHA PREMIUM</p>
        <strong>
          {live > 0
            ? `${live} ${live === 1 ? "partido en vivo" : "partidos en vivo"} ahora mismo`
            : "Predicciones sin límite y análisis en vivo"}
        </strong>
        <span className="muted">
          {live > 0
            ? "El análisis en vivo, el menú de mayor probabilidad y el constructor son parte de Premium."
            : "Sin tope diario, con el menú de mayor probabilidad y el constructor de picks."}
        </span>
      </div>
      <Link className="secondary-button" href="/settings">Ver qué incluye</Link>
    </aside>
  );
}

/**
 * Cierre de una pantalla que acabó de entregar valor.
 *
 * Va **después** del contenido, nunca antes: pedir la compra encima de lo que
 * el usuario vino a leer es lo que convierte una oferta en un estorbo.
 */
export function PremiumFooterCta() {
  const quota = useQuotaState();
  if (!quota) return null;

  return (
    <aside className="premium-banner slim">
      <span className="muted">
        {quota.remaining > 0
          ? `Te ${quota.remaining === 1 ? "queda" : "quedan"} ${quota.remaining} de ${quota.limit} predicciones hoy.`
          : "Has usado tus predicciones de hoy."}
        {" "}Premium quita el límite y abre el análisis en vivo.
      </span>
      <SubscribeButton label="Activar Premium" />
    </aside>
  );
}
