"use client";

import { useQuery } from "@tanstack/react-query";

import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { api } from "@/lib/client-api";

type Model = { name: string; mode: "official" | "shadow"; scope: string };
type Payload = {
  status: string;
  models: Model[];
  hawkes_policy?: { version: string; allowed_league_count: number; rho_goal: number; rho_next_event: number };
};

export default function ModelsPage() {
  const query = useQuery({ queryKey: ["models"], queryFn: () => api<Payload>("/api/models") });
  return (
    <>
      <PageHeader eyebrow="INVENTARIO OPERATIVO" title="Modelos" />
      {query.isError ? (
        <StatePanel title="Inventario no disponible">La interfaz no declarará un modelo como activo sin confirmación de la API.</StatePanel>
      ) : (
        <div className="stack">
          {query.data?.models.map((model) => (
            <article className="model-card" key={model.name}>
              <div className="model-card-header"><h3>{model.name}</h3><ShadowBadge official={model.mode === "official"} /></div>
              <p>{model.scope.replaceAll("_", " ")}</p>
            </article>
          ))}
          {query.data?.hawkes_policy ? (
            <article className="data-panel">
              <p className="eyebrow">POLÍTICA HAWKES</p>
              <h3>{query.data.hawkes_policy.allowed_league_count} ligas admitidas</h3>
              <p className="muted">ρ goles {query.data.hawkes_policy.rho_goal} · ρ próximo evento {query.data.hawkes_policy.rho_next_event}</p>
            </article>
          ) : null}
        </div>
      )}
    </>
  );
}
