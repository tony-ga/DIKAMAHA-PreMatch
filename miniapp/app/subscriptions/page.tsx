"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";

import { useAuth } from "@/components/providers";
import { PageHeader, ShadowBadge, StatePanel } from "@/components/ui";
import { api, type Catalog, queryString } from "@/lib/client-api";
import { alertRuleTypes, marketKeys } from "@/lib/validation";

type Subscription = {
  id: string;
  ruleType: string;
  fixtureId?: string;
  leagueSlug?: string;
  marketKey?: string;
  threshold?: string;
  cooldownSeconds: number;
  enabled: boolean;
};

const labels: Record<string, string> = {
  kickoff: "Inicio del partido",
  score_change: "Cambio de marcador",
  status_change: "Cambio de estado live",
  probability_delta: "Cambio de probabilidad",
  model_status: "Estado de modelos",
  market_threshold: "Umbral de mercado shadow",
  fixture_presence: "Aparición/desaparición live",
};

export default function SubscriptionsPage() {
  const { csrfToken } = useAuth();
  const client = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["subscriptions"],
    queryFn: () => api<{ subscriptions: Subscription[] }>("/api/subscriptions"),
  });
  const create = useMutation({
    mutationFn: (payload: unknown) => api("/api/subscriptions", { method: "POST", body: JSON.stringify(payload) }, csrfToken),
    onSuccess: () => { setError(null); void client.invalidateQueries({ queryKey: ["subscriptions"] }); },
    onError: (value) => setError(value instanceof Error ? value.message : "subscription_failed"),
  });
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: unknown }) => api(`/api/subscriptions/${id}`, { method: "PATCH", body: JSON.stringify(payload) }, csrfToken),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["subscriptions"] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/subscriptions/${id}`, { method: "DELETE" }, csrfToken),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["subscriptions"] }),
  });

  const [checkingFixture, setCheckingFixture] = useState(false);

  // DEC-192: el worker de alertas (`miniapp/worker/alerts.ts`) sólo evalúa
  // fixtures que aparecen en su barrido de `/v1/live` por liga -y, para
  // reglas todavía no en vivo, en el barrido próximo de `/v1/upcoming`-;
  // antes el formulario aceptaba cualquier `fixtureId` sin comprobarlo
  // contra ninguno de los dos, así que un ID mal copiado o de un partido ya
  // terminado se guardaba igual y nunca se evaluaba, sin ningún error
  // visible en ese momento. Se consulta ambos catálogos -no sólo "en
  // vivo"- porque crear la alerta antes del kickoff es el caso de uso
  // normal, no la excepción.
  async function fixtureExists(fixtureId: string, leagueSlug: string): Promise<boolean> {
    const filters = queryString({ leagues: leagueSlug, limit: 20 });
    const [upcoming, live] = await Promise.allSettled([
      api<Catalog>(`/api/upcoming${filters}`),
      api<Catalog>(`/api/live${filters}`),
    ]);
    const catalogs = [upcoming, live]
      .filter((result): result is PromiseFulfilledResult<Catalog> => result.status === "fulfilled")
      .map((result) => result.value);
    return catalogs.some((catalog) => catalog.fixtures.some(
      (fixture) => String(fixture.match_id) === fixtureId,
    ));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const ruleType = String(data.get("ruleType"));
    const thresholdRule = ["probability_delta", "market_threshold"].includes(ruleType);
    const fixtureId = String(data.get("fixtureId") || "") || undefined;
    const leagueSlug = String(data.get("leagueSlug") || "") || undefined;
    if (fixtureId && leagueSlug) {
      setError(null);
      setCheckingFixture(true);
      let exists = false;
      try {
        exists = await fixtureExists(fixtureId, leagueSlug);
      } catch {
        // Sin poder confirmar, se deja pasar: es preferible una alerta que
        // nunca dispare a bloquear el alta por un catálogo caído.
        exists = true;
      } finally {
        setCheckingFixture(false);
      }
      if (!exists) {
        setError(
          `No encontramos el partido ${fixtureId} en ${leagueSlug} entre los próximos ni los que están en vivo. Revisa el ID o crea la alerta por liga.`,
        );
        return;
      }
    }
    create.mutate({
      ruleType,
      fixtureId,
      leagueSlug,
      marketKey: thresholdRule ? String(data.get("marketKey")) : undefined,
      comparator: ruleType === "probability_delta" ? "delta" : thresholdRule ? String(data.get("comparator")) : undefined,
      threshold: thresholdRule ? Number(data.get("threshold")) / 100 : undefined,
      period: thresholdRule ? "live" : undefined,
      cooldownSeconds: Math.max(300, Number(data.get("cooldownSeconds")) * 60),
      enabled: true,
    });
  }

  function editCooldown(event: FormEvent<HTMLFormElement>, id: string) {
    event.preventDefault();
    const minutes = Number(new FormData(event.currentTarget).get("cooldownMinutes"));
    update.mutate({ id, payload: { cooldownSeconds: Math.max(300, minutes * 60) } });
  }

  return (
    <>
      <PageHeader eyebrow="REGLAS PERSONALES" title="Alertas" />
      <article className="data-panel">
        <div className="model-card-header"><h3>Nueva suscripción</h3><ShadowBadge /></div>
        <form className="form-grid" onSubmit={submit} style={{ marginTop: 16 }}>
          <div className="field"><label htmlFor="ruleType">Tipo</label><select id="ruleType" name="ruleType">{alertRuleTypes.map((rule) => <option key={rule} value={rule}>{labels[rule]}</option>)}</select></div>
          <div className="field"><label htmlFor="fixtureId">ID de partido (opcional si indicas liga)</label><input id="fixtureId" name="fixtureId" inputMode="numeric" placeholder="401880614" /></div>
          <div className="field"><label htmlFor="leagueSlug">Liga</label><input id="leagueSlug" name="leagueSlug" required placeholder="eng.2" /></div>
          <div className="field"><label htmlFor="marketKey">Mercado para reglas probabilísticas</label><select id="marketKey" name="marketKey">{marketKeys.map((market) => <option key={market} value={market}>{market.replaceAll("_", " ")}</option>)}</select></div>
          <div className="field"><label htmlFor="comparator">Condición</label><select id="comparator" name="comparator"><option value="gte">Sube o iguala</option><option value="lte">Baja o iguala</option><option value="delta">Cambio absoluto</option></select></div>
          <div className="field"><label htmlFor="threshold">Umbral porcentual</label><input id="threshold" name="threshold" type="number" min="0" max="100" step="1" defaultValue="5" /></div>
          <div className="field"><label htmlFor="cooldownSeconds">Cooldown en minutos</label><input id="cooldownSeconds" name="cooldownSeconds" type="number" min="5" max="1440" defaultValue="5" /></div>
          {error ? <p style={{ color: "var(--danger)", margin: 0 }}>{error}</p> : null}
          <button className="primary-button" type="submit" disabled={create.isPending || checkingFixture}>{checkingFixture ? "Verificando partido…" : create.isPending ? "Guardando…" : "Crear alerta"}</button>
        </form>
      </article>

      <div style={{ height: 20 }} />
      {query.data?.subscriptions.length ? (
        <div className="stack">
          {query.data.subscriptions.map((subscription) => (
            <article className="model-card" key={subscription.id}>
              <div className="subscription-row">
                <div><strong>{labels[subscription.ruleType] ?? subscription.ruleType}</strong><p>{subscription.fixtureId ? `Partido ${subscription.fixtureId}` : `Liga ${subscription.leagueSlug}`} · cooldown {subscription.cooldownSeconds / 60} min</p></div>
                <button className={subscription.enabled ? "switch on" : "switch"} onClick={() => update.mutate({ id: subscription.id, payload: { enabled: !subscription.enabled } })} aria-label={subscription.enabled ? "Pausar alerta" : "Activar alerta"}>{subscription.enabled ? "ON" : "OFF"}</button>
              </div>
              <form className="inline-edit" onSubmit={(event) => editCooldown(event, subscription.id)}>
                <label htmlFor={`cooldown-${subscription.id}`}>Editar cooldown</label>
                <input id={`cooldown-${subscription.id}`} name="cooldownMinutes" type="number" min="5" max="1440" defaultValue={subscription.cooldownSeconds / 60} />
                <button className="secondary-button" type="submit" disabled={update.isPending}>Guardar</button>
              </form>
              <button className="danger-button" onClick={() => remove.mutate(subscription.id)} disabled={remove.isPending}>Eliminar</button>
            </article>
          ))}
        </div>
      ) : <StatePanel title="Sin alertas configuradas">Puedes crear hasta 20 reglas activas. Los mercados se notifican siempre como shadow.</StatePanel>}
    </>
  );
}
