"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { useEntitlement } from "@/components/premium-gate";
import { useAuth } from "@/components/providers";
import { PageHeader, SegmentedControl, StatePanel } from "@/components/ui";
import { api } from "@/lib/client-api";

type AdminUser = {
  telegramUserId: number;
  username: string | null;
  firstName: string;
  status: "pending" | "active" | "blocked";
  role: "user" | "admin";
  plan: "free" | "premium";
  planSource: "default" | "stars" | "grandfathered" | "admin" | "refunded";
  planExpiresAt: string | null;
  firstSeenAt: string;
  lastSeenAt: string;
};

const PLAN_FILTERS = [
  { value: "all", label: "Todos" },
  { value: "premium", label: "Premium" },
  { value: "free", label: "Gratuito" },
  { value: "pending", label: "Pendientes" },
];

function formatDay(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? date.toLocaleDateString("es-MX", { day: "2-digit", month: "short", year: "numeric" })
    : "—";
}

function planLabel(user: AdminUser): string {
  if (user.plan !== "premium") return "Gratuito";
  if (user.planSource === "admin") return "Premium · admin";
  if (user.planSource === "grandfathered") return `Premium · heredado hasta ${formatDay(user.planExpiresAt)}`;
  if (user.planSource === "stars") return `Premium · hasta ${formatDay(user.planExpiresAt)}`;
  return "Premium";
}

/**
 * Panel de administración de cuentas.
 *
 * Antes de esta pantalla, ver quién usa la Mini App -y quién es premium, y
 * quién es administrador- exigía una consulta SQL directa contra Railway
 * (`docs/runbooks/telegram_stars_subscriptions.md`). Esa vía sigue siendo
 * válida para lo que esta pantalla no cubre; para lo que sí cubre, ya no hace
 * falta salir de la Mini App.
 *
 * El servidor es quien de verdad decide el acceso -esta página sólo evita
 * pintar la tabla para quien no es administrador; `authorizeRequest` +
 * `resolveEntitlement` en `/api/admin/users` son la autoridad real.
 */
export default function AdminPage() {
  const { csrfToken } = useAuth();
  const { data: entitlement, isLoading: entitlementLoading } = useEntitlement();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [planFilter, setPlanFilter] = useState("all");

  const isAdmin = entitlement?.role === "admin";
  const users = useQuery({
    queryKey: ["admin-users"],
    queryFn: () => api<{ users: AdminUser[] }>("/api/admin/users"),
    enabled: isAdmin,
    staleTime: 15_000,
  });

  const update = useMutation({
    mutationFn: ({ userId, changes }: {
      userId: number; changes: Partial<Pick<AdminUser, "status" | "role" | "plan">>;
    }) => api(`/api/admin/users/${userId}`, {
      method: "PATCH", body: JSON.stringify(changes),
    }, csrfToken),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-users"] }),
  });

  const filtered = useMemo(() => {
    const rows = users.data?.users ?? [];
    const needle = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (planFilter === "premium" && row.plan !== "premium") return false;
      if (planFilter === "free" && row.plan !== "free") return false;
      if (planFilter === "pending" && row.status !== "pending") return false;
      if (!needle) return true;
      return (
        String(row.telegramUserId).includes(needle)
        || row.firstName.toLowerCase().includes(needle)
        || (row.username ?? "").toLowerCase().includes(needle)
      );
    });
  }, [users.data, search, planFilter]);

  if (entitlementLoading) return null;
  if (!isAdmin) {
    return (
      <>
        <PageHeader eyebrow="ADMINISTRACIÓN" title="Acceso restringido" />
        <StatePanel title="Esta sección es sólo para administradores">
          Si crees que deberías tener acceso, pide a un administrador que te
          promueva desde aquí mismo.
        </StatePanel>
      </>
    );
  }

  const premiumCount = users.data?.users.filter((row) => row.plan === "premium").length ?? 0;
  const pendingCount = users.data?.users.filter((row) => row.status === "pending").length ?? 0;

  return (
    <>
      <PageHeader eyebrow="ADMINISTRACIÓN" title="Cuentas" action={
        <button className="icon-button" onClick={() => void users.refetch()} aria-label="Actualizar">↻</button>
      } />
      <div className="metric-grid compact-metrics">
        <div className="metric"><span>Cuentas</span><strong>{users.data?.users.length ?? "—"}</strong></div>
        <div className="metric"><span>Premium</span><strong>{premiumCount}</strong></div>
        <div className="metric"><span>Pendientes</span><strong>{pendingCount}</strong></div>
      </div>
      <div className="form-grid filter-grid">
        <div className="field">
          <label htmlFor="admin-search">Buscar</label>
          <input
            id="admin-search" value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Nombre, usuario o ID de Telegram"
          />
        </div>
      </div>
      <SegmentedControl
        label="Plan" options={PLAN_FILTERS} value={planFilter} onChange={setPlanFilter}
      />
      {users.isError ? (
        <StatePanel title="No se pudo cargar la lista" action={
          <button className="primary-button" onClick={() => void users.refetch()}>Reintentar</button>
        }>
          Inténtalo de nuevo en un momento.
        </StatePanel>
      ) : users.isLoading ? (
        <StatePanel title="Cargando cuentas">Un momento.</StatePanel>
      ) : filtered.length === 0 ? (
        <StatePanel title="Sin resultados">Ninguna cuenta coincide con el filtro.</StatePanel>
      ) : (
        <div className="stack">
          {filtered.map((row) => (
            <article className="data-panel admin-user-row" key={row.telegramUserId}>
              <div>
                <strong>{row.firstName}</strong>
                {row.username ? <span className="muted"> · @{row.username}</span> : null}
                <div className="muted" style={{ fontSize: ".72rem" }}>
                  ID {row.telegramUserId} · visto {formatDay(row.lastSeenAt)}
                </div>
              </div>
              <div className="admin-user-tags">
                <span className={`tag tag-${row.status}`}>{row.status}</span>
                {row.role === "admin" ? <span className="tag tag-admin">admin</span> : null}
                <span className="muted">{planLabel(row)}</span>
              </div>
              <div className="admin-user-actions">
                {row.status === "pending" ? (
                  <button
                    className="secondary-button" disabled={update.isPending}
                    onClick={() => update.mutate({
                      userId: row.telegramUserId, changes: { status: "active" },
                    })}
                  >Aprobar</button>
                ) : row.status === "active" ? (
                  <button
                    className="secondary-button" disabled={update.isPending}
                    onClick={() => update.mutate({
                      userId: row.telegramUserId, changes: { status: "blocked" },
                    })}
                  >Bloquear</button>
                ) : (
                  <button
                    className="secondary-button" disabled={update.isPending}
                    onClick={() => update.mutate({
                      userId: row.telegramUserId, changes: { status: "active" },
                    })}
                  >Desbloquear</button>
                )}
                <button
                  className="secondary-button" disabled={update.isPending}
                  onClick={() => update.mutate({
                    userId: row.telegramUserId,
                    changes: { role: row.role === "admin" ? "user" : "admin" },
                  })}
                >{row.role === "admin" ? "Quitar admin" : "Hacer admin"}</button>
                <button
                  className="secondary-button" disabled={update.isPending}
                  onClick={() => update.mutate({
                    userId: row.telegramUserId,
                    changes: { plan: row.plan === "premium" ? "free" : "premium" },
                  })}
                >{row.plan === "premium" ? "Quitar premium" : "Dar premium"}</button>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
