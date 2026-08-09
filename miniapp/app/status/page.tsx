"use client";

import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/components/providers";
import { PageHeader, StatePanel } from "@/components/ui";
import { api } from "@/lib/client-api";

type Readiness = { ready?: boolean; status?: string; service_version?: string; contract_version?: string };

export default function StatusPage() {
  const { user } = useAuth();
  const query = useQuery({ queryKey: ["readiness"], queryFn: () => api<Readiness>("/api/readiness"), refetchInterval: 30_000 });
  return (
    <>
      <PageHeader eyebrow="DIAGNÓSTICO DE CONEXIÓN" title="Estado del sistema" action={<button className="icon-button" onClick={() => void query.refetch()} aria-label="Actualizar estado">↻</button>} />
      {query.isError ? <StatePanel title="API no disponible" action={<button className="primary-button" onClick={() => void query.refetch()}>Reintentar</button>}>La sesión sigue protegida; ninguna clave se envía al navegador.</StatePanel> : (
        <div className="stack">
          <article className="status-card"><span className={query.data?.ready ? "status-lamp online" : "status-lamp"} /><div><p className="eyebrow">API DIKAMAHA</p><h3>{query.isLoading ? "Comprobando" : query.data?.ready ? "Conectada" : "No lista"}</h3><p>Contrato {query.data?.contract_version ?? "N/D"} · versión {query.data?.service_version ?? "N/D"}</p></div></article>
          <article className="status-card"><span className="status-lamp online" /><div><p className="eyebrow">SESIÓN TELEGRAM</p><h3>Validada</h3><p>{user?.firstName} · ID {user?.id}</p></div></article>
          <div className="notice">La prueba sigue la ruta navegador → BFF → API DIKAMAHA. El proveedor y la API key nunca llegan al cliente.</div>
        </div>
      )}
    </>
  );
}
