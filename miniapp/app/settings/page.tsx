"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { useAuth } from "@/components/providers";
import { PageHeader, StatePanel } from "@/components/ui";
import { api } from "@/lib/client-api";

type Favorite = { entityType: string; entityId: string; label: string };

export default function SettingsPage() {
  const { user } = useAuth();
  const favorites = useQuery({ queryKey: ["favorites"], queryFn: () => api<{ favorites: Favorite[] }>("/api/favorites") });
  return (
    <>
      <PageHeader eyebrow="CUENTA Y PREFERENCIAS" title="Ajustes" />
      <div className="stack">
        <article className="data-panel">
          <p className="eyebrow">IDENTIDAD TELEGRAM</p>
          <h3>{user?.firstName}</h3>
          <p className="muted">ID {user?.id} · sesión validada criptográficamente</p>
        </article>
        <article className="data-panel">
          <p className="eyebrow">EXPERIENCIA LIVE</p>
          <h3>Refresco cada 25 segundos</h3>
          <p className="muted">Tema automático de Telegram, navegación persistente y fallback al bot nativo.</p>
        </article>
        <article className="data-panel">
          <p className="eyebrow">FAVORITOS · {favorites.data?.favorites.length ?? 0}/10</p>
          {favorites.data?.favorites.length ? (
            <div className="stack">{favorites.data.favorites.map((favorite) => <div className="subscription-row" key={`${favorite.entityType}:${favorite.entityId}`}><span>{favorite.label}</span><small className="muted">{favorite.entityType}</small></div>)}</div>
          ) : <p className="muted">Marca ☆ en cualquier partido para guardarlo aquí.</p>}
        </article>
        <div className="settings-links">
          <Link className="secondary-button" href="/subscriptions">Alertas</Link>
          <Link className="secondary-button" href="/status">Estado del sistema</Link>
          <Link className="secondary-button" href="/help">Ayuda</Link>
        </div>
        <StatePanel title="Uso responsable">DIKAMAHA comunica probabilidades analíticas. No ofrece cuotas, stakes ni ejecución de apuestas.</StatePanel>
      </div>
    </>
  );
}
