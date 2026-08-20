"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";

import {
  PremiumUpsell,
  SubscribeButton,
  formatDay,
  useEntitlement,
} from "@/components/premium-gate";
import { useAuth } from "@/components/providers";
import { PageHeader, StatePanel } from "@/components/ui";
import { api } from "@/lib/client-api";

type Favorite = { entityType: string; entityId: string; label: string };

/**
 * Panel de plan.
 *
 * Con `enforced: false` -el cobro apagado- no se pinta nada: enseñar un precio
 * y un botón de compra cuando nada está gateado invitaría a pagar por lo que
 * ya es gratis.
 */
function PlanPanel() {
  const { csrfToken } = useAuth();
  const queryClient = useQueryClient();
  const { data } = useEntitlement();
  const cancel = useMutation({
    mutationFn: () => api<{ status: string }>(
      "/api/billing/cancel", { method: "POST", body: "{}" }, csrfToken),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["entitlement"] }),
  });
  // Cancelar, cambiar tarjeta y ver recibos se delegan en el portal alojado de
  // Stripe: cancelar tiene que ser tan fácil como comprar, y el portal ya lo
  // es sin que ningún dato de pago pase por aquí.
  const portal = useMutation({
    mutationFn: () => api<{ url: string }>(
      "/api/billing/stripe/portal", { method: "POST", body: "{}" }, csrfToken),
    onSuccess: ({ url }) => { window.location.assign(url); },
  });

  if (!data || !data.enforced) return null;

  if (data.plan === "free") {
    return (
      <PremiumUpsell
        headline={`${data.starsAmount} ⭐ al mes`}
        detail={
          data.quota
            ? `Hoy has usado ${data.quota.used} de tus ${data.quota.limit} predicciones.`
            : undefined
        }
      />
    );
  }

  // Ser honesto con quien heredó el acceso evita la sorpresa el día del
  // vencimiento, que es cuando se pierde a un usuario para siempre.
  const heredado = data.planSource === "grandfathered";
  const perpetuo = data.planSource === "admin";
  return (
    <article className="data-panel">
      <p className="eyebrow">DIKAMAHA PREMIUM</p>
      <h3>{heredado ? "Acceso heredado" : "Premium activo"}</h3>
      <p className="muted">
        {perpetuo ? "Acceso permanente de administración."
          : heredado ? `Acceso incluido hasta el ${formatDay(data.expiresAt)}.`
          : `Se renueva el ${formatDay(data.expiresAt)}.`}
      </p>
      {data.planSource === "stars" ? (
        <button
          className="secondary-button"
          disabled={cancel.isPending}
          onClick={() => {
            if (window.confirm(
              "¿Cancelar la renovación? Conservas Premium hasta el final del "
              + "periodo ya pagado.")) cancel.mutate();
          }}
        >
          {cancel.isPending ? "Cancelando…" : "Cancelar renovación"}
        </button>
      ) : null}
      {data.planSource === "stripe" ? (
        <button
          className="secondary-button"
          disabled={portal.isPending}
          onClick={() => portal.mutate()}
        >
          {portal.isPending ? "Abriendo…" : "Gestionar suscripción"}
        </button>
      ) : null}
      {heredado ? <SubscribeButton label={`Suscribirme · ${data.starsAmount} ⭐ al mes`} /> : null}
    </article>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const { data: entitlement } = useEntitlement();
  const favorites = useQuery({ queryKey: ["favorites"], queryFn: () => api<{ favorites: Favorite[] }>("/api/favorites") });
  const premium = entitlement?.plan === "premium";
  return (
    <>
      <PageHeader eyebrow="CUENTA Y PREFERENCIAS" title="Ajustes" />
      <div className="stack">
        <article className="data-panel">
          <p className="eyebrow">IDENTIDAD TELEGRAM</p>
          <h3>{user?.firstName}</h3>
          <p className="muted">ID {user?.id} · sesión validada criptográficamente</p>
        </article>
        <PlanPanel />
        <article className="data-panel">
          <p className="eyebrow">EXPERIENCIA LIVE</p>
          <h3>Refresco cada 25 segundos</h3>
          <p className="muted">Tema automático de Telegram, navegación persistente y fallback al bot nativo.</p>
        </article>
        <article className="data-panel">
          <p className="eyebrow">
            FAVORITOS · {favorites.data?.favorites.length ?? 0}
            {premium ? " (sin límite)" : "/10"}
          </p>
          {favorites.data?.favorites.length ? (
            <div className="stack">{favorites.data.favorites.map((favorite) => <div className="subscription-row" key={`${favorite.entityType}:${favorite.entityId}`}><span>{favorite.label}</span><small className="muted">{favorite.entityType}</small></div>)}</div>
          ) : <p className="muted">Marca ☆ en cualquier partido para guardarlo aquí.</p>}
        </article>
        <div className="settings-links">
          <Link className="secondary-button" href="/subscriptions">Alertas</Link>
          <Link className="secondary-button" href="/status">Estado del sistema</Link>
          <Link className="secondary-button" href="/help">Ayuda</Link>
          {entitlement?.role === "admin" ? (
            <Link className="secondary-button" href="/admin">Administración</Link>
          ) : null}
        </div>
        <StatePanel title="Uso responsable">DIKAMAHA comunica probabilidades analíticas. No ofrece cuotas, stakes ni ejecución de apuestas.</StatePanel>
      </div>
    </>
  );
}
