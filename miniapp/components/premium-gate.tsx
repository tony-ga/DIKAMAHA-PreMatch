"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useCallback, useState } from "react";

import { useAuth } from "@/components/providers";
import { api } from "@/lib/client-api";

export type EntitlementView = {
  plan: "free" | "premium";
  planSource: "default" | "stars" | "grandfathered" | "admin" | "refunded";
  role: "user" | "admin";
  expiresAt: string | null;
  enforced: boolean;
  starsAmount: number;
  quota: { used: number; limit: number; remaining: number } | null;
};

/**
 * Titularidad vigente del usuario.
 *
 * Se consulta al servidor y no se lee del `plan` de la sesión: esa cookie dura
 * 30 días y no se relee, así que para una suscripción mensual estaría obsoleta
 * en los dos sentidos -quien cancela y quien acaba de pagar-.
 */
export function useEntitlement() {
  const { csrfToken } = useAuth();
  return useQuery({
    queryKey: ["entitlement"],
    queryFn: () => api<EntitlementView>("/api/billing/entitlement"),
    enabled: Boolean(csrfToken),
    staleTime: 30_000,
  });
}

export function usePremium(): boolean {
  const { data } = useEntitlement();
  // Mientras no se sabe, se asume premium: pintar un muro y retirarlo medio
  // segundo después es peor que el parpadeo inverso, y el servidor gatea de
  // todos modos.
  return data ? data.plan === "premium" : true;
}

export function formatDay(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "";
  return date.toLocaleDateString("es-MX", {
    day: "2-digit", month: "long", year: "numeric",
  });
}

/**
 * Panel de venta del nivel de pago.
 *
 * Vende **acceso y volumen**, nunca retorno: el proyecto tiene congelados ROI,
 * Kelly, stakes y cuotas, y la superficie comercial es exactamente donde más
 * tienta saltarse esa restricción. El aviso de uso responsable va aquí dentro
 * por la misma razón.
 */
export function PremiumUpsell({ headline, detail }: {
  headline: string;
  detail?: ReactNode;
}) {
  const { data } = useEntitlement();
  const stars = data?.starsAmount ?? 250;
  return (
    <article className="data-panel premium-upsell">
      <p className="eyebrow">DIKAMAHA PREMIUM</p>
      <h3>{headline}</h3>
      {detail ? <p className="muted">{detail}</p> : null}
      <ul className="premium-benefits">
        <li>Predicciones pre-match sin límite</li>
        <li>Análisis en vivo</li>
        <li>Menú de mayor probabilidad completo</li>
        <li>Constructor de picks con probabilidad conjunta</li>
        <li>Favoritos y alertas sin tope</li>
      </ul>
      <SubscribeButton label={`Activar Premium · ${stars} ⭐ al mes`} />
      <p className="muted premium-disclaimer">
        DIKAMAHA publica análisis estadístico. No es asesoramiento financiero
        ni una recomendación de apuesta.
      </p>
    </article>
  );
}

/**
 * Botón de compra.
 *
 * Tras un `paid` no se da el alta por hecha: la confirmación viaja por
 * Telegram hasta el bot y de ahí a la base, así que se reconsulta la
 * titularidad unas cuantas veces. Normalmente tarda menos de un segundo, pero
 * la interfaz no puede depender de que ese salto no falle nunca.
 */
export function SubscribeButton({ label }: { label: string }) {
  const { csrfToken } = useAuth();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string | null>(null);

  const pollEntitlement = useCallback(() => {
    let attempts = 0;
    const tick = () => {
      attempts += 1;
      void queryClient.invalidateQueries({ queryKey: ["entitlement"] });
      if (attempts < 4) setTimeout(tick, 2_000);
    };
    tick();
  }, [queryClient]);

  const mutation = useMutation({
    mutationFn: () => api<{ link: string; starsAmount: number }>(
      "/api/billing/invoice", { method: "POST", body: "{}" }, csrfToken),
    onSuccess: ({ link }) => {
      const webApp = window.Telegram?.WebApp;
      if (!webApp?.openInvoice) {
        setStatus("Abre DIKAMAHA desde Telegram para completar el pago.");
        return;
      }
      webApp.openInvoice(link, (result) => {
        if (result === "paid") {
          setStatus("Pago recibido. Activando tu Premium…");
          pollEntitlement();
        } else if (result === "failed") {
          setStatus("El pago no se completó.");
        } else {
          setStatus(null);
        }
      });
    },
    onError: () => setStatus("No pudimos abrir el pago. Inténtalo en un momento."),
  });

  return (
    <>
      <button
        className="primary-button"
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
      >
        {mutation.isPending ? "Preparando…" : label}
      </button>
      {status ? <p className="muted">{status}</p> : null}
    </>
  );
}

/**
 * Muro para una pantalla completa de pago.
 *
 * Sin contenido borroso a propósito: un placeholder difuminado invita a
 * intentar leerlo y convierte el muro en un reto en lugar de una oferta.
 */
export function PremiumGate({ headline, detail, children }: {
  headline: string;
  detail?: ReactNode;
  children?: ReactNode;
}) {
  const { data, isLoading } = useEntitlement();
  if (isLoading || !data || data.plan === "premium") return <>{children}</>;
  return <PremiumUpsell headline={headline} detail={detail} />;
}

/** Contador de predicciones restantes. Invisible para quien ya paga. */
export function QuotaChip() {
  const { data } = useEntitlement();
  if (!data || data.plan === "premium" || !data.quota) return null;
  const { used, limit } = data.quota;
  return (
    <span className="quota-chip" title="Predicciones usadas hoy">
      {used}/{limit}
    </span>
  );
}
