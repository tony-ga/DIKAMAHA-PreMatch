import { createHash } from "node:crypto";
import { pathToFileURL } from "node:url";
import postgres from "postgres";

type JsonValue = null | string | number | boolean | Json | readonly JsonValue[];
type Json = { readonly [key: string]: JsonValue | undefined };
type Subscription = {
  id: string;
  user_id: number;
  rule_type: string;
  fixture_id: string | null;
  league_slug: string | null;
  market_key: string | null;
  comparator: string | null;
  threshold: string | null;
  cooldown_seconds: number;
  last_observation: Json | null;
  last_triggered_at: Date | null;
};

const required = [
  "TELEGRAM_BOT_TOKEN",
  "DIKAMAHA_BOT_API_URL",
  "DIKAMAHA_API_KEY",
  "DATABASE_URL",
] as const;

function config() {
  for (const name of required) {
    if (!process.env[name]) throw new Error(`missing_${name.toLowerCase()}`);
  }
  const pollSeconds = Number(process.env.TELEGRAM_ALERT_POLL_SECONDS ?? "30");
  if (!Number.isFinite(pollSeconds) || pollSeconds < 15 || pollSeconds > 300) {
    throw new Error("telegram_alert_poll_seconds_invalid");
  }
  const apiUrl = process.env.DIKAMAHA_BOT_API_URL!.replace(/\/$/, "");
  if (!apiUrl.startsWith("https://")) {
    throw new Error("dikamaha_bot_api_url_https_required");
  }
  return {
    botToken: process.env.TELEGRAM_BOT_TOKEN!,
    apiUrl,
    apiKey: process.env.DIKAMAHA_API_KEY!,
    databaseUrl: process.env.DATABASE_URL!,
    pollSeconds,
    enabled: process.env.MINIAPP_ALERTS_ENABLED === "true",
  };
}

async function dikamaha(path: string, options: RequestInit = {}): Promise<Json> {
  const cfg = config();
  const response = await fetch(`${cfg.apiUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Dikamaha-Key": cfg.apiKey,
      "X-Request-ID": `miniapp-alert-${crypto.randomUUID()}`,
      ...options.headers,
    },
    signal: AbortSignal.timeout(35_000),
  });
  if (!response.ok) throw new Error(`dikamaha_${response.status}`);
  return await response.json() as Json;
}

function records(value: unknown): Json[] {
  return Array.isArray(value)
    ? value.filter((item): item is Json => Boolean(item) && typeof item === "object")
    : [];
}

function numeric(value: unknown): number | null {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 1 ? number : null;
}

function liveMarket(prediction: Json | null, marketKey: string | null): number | null {
  if (!prediction || !marketKey) return null;
  const official = prediction.official_live_prediction;
  if (official && typeof official === "object") {
    const markets = (official as Json).markets;
    if (markets && typeof markets === "object") {
      const found = numeric((markets as Json)[marketKey]);
      if (found !== null) return found;
    }
  }
  for (const layerName of ["experimental_combined_live", "experimental_markov_live"]) {
    const layer = prediction[layerName];
    if (!layer || typeof layer !== "object") continue;
    const markets = (layer as Json).markets;
    if (markets && typeof markets === "object") {
      const found = numeric((markets as Json)[marketKey]);
      if (found !== null) return found;
    }
    const direct = numeric((layer as Json)[marketKey]);
    if (direct !== null) return direct;
  }
  const shadow = prediction.experimental_team_markets;
  if (shadow && typeof shadow === "object") {
    const probabilities = (shadow as Json).probabilities;
    if (probabilities && typeof probabilities === "object") {
      return numeric((probabilities as Json)[marketKey]);
    }
  }
  return null;
}

function observation(
  subscription: Subscription,
  fixture: Json | null,
  prediction: Json | null,
): Json {
  const present = fixture !== null;
  switch (subscription.rule_type) {
    case "fixture_presence":
      return { present };
    case "kickoff":
      return { started: ["in", "in_progress", "live"].includes(
        String(fixture?.provider_status ?? "").trim().toLowerCase(),
      ) };
    case "score_change":
      return {
        present,
        home: fixture ? Number(fixture.home_score ?? 0) : null,
        away: fixture ? Number(fixture.away_score ?? 0) : null,
      };
    case "status_change":
      return { present, status: String(fixture?.provider_status ?? "unavailable") };
    case "model_status":
      return {
        present,
        official_source: prediction?.official_source ?? "unavailable",
        official: (prediction?.official_live_prediction as Json | undefined)?.status ?? "unavailable",
        engine_audit: ((prediction?.live_probability_engine as Json | undefined)?.audit as Json | undefined)?.passed ?? false,
        markov: (prediction?.experimental_markov_live as Json | undefined)?.status ?? "unavailable",
        hawkes: (prediction?.experimental_hawkes_residual as Json | undefined)?.status ?? "unavailable",
        combined: (prediction?.experimental_combined_live as Json | undefined)?.status ?? "unavailable",
      };
    case "probability_delta":
    case "market_threshold":
      return { value: liveMarket(prediction, subscription.market_key) };
    default:
      return { present };
  }
}

function changed(previous: Json | null, current: Json): boolean {
  return previous !== null && JSON.stringify(previous) !== JSON.stringify(current);
}

export function shouldTrigger(subscription: Subscription, current: Json): boolean {
  const previous = subscription.last_observation;
  if (["score_change", "status_change", "model_status"].includes(
    subscription.rule_type,
  ) && current.present !== true) return false;
  if (subscription.rule_type === "probability_delta") {
    const before = numeric(previous?.value);
    const after = numeric(current.value);
    const threshold = Number(subscription.threshold ?? 0);
    return before !== null && after !== null && Math.abs(after - before) >= threshold;
  }
  if (subscription.rule_type === "market_threshold") {
    const before = numeric(previous?.value);
    const after = numeric(current.value);
    const threshold = Number(subscription.threshold ?? 0);
    if (after === null) return false;
    const meets = subscription.comparator === "lte" ? after <= threshold : after >= threshold;
    const metBefore = before !== null
      && (subscription.comparator === "lte" ? before <= threshold : before >= threshold);
    return meets && !metBefore;
  }
  if (subscription.rule_type === "kickoff") {
    return previous?.started === false && current.started === true;
  }
  return changed(previous, current);
}

function eventKey(subscription: Subscription, current: Json): string {
  const digest = createHash("sha256")
    .update(JSON.stringify(current))
    .digest("hex")
    .slice(0, 20);
  return `${subscription.rule_type}:${subscription.fixture_id ?? subscription.league_slug}:${digest}`;
}

function message(subscription: Subscription, current: Json): string {
  const scope = subscription.fixture_id
    ? `Partido ${subscription.fixture_id}`
    : `Liga ${subscription.league_slug}`;
  const value = current.value === undefined
    ? JSON.stringify(current)
    : `${Math.round(Number(current.value) * 100)}%`;
  const modelNote = ["market_threshold", "probability_delta", "model_status"]
    .includes(subscription.rule_type)
    ? "\n\n🧠 Señal del motor probabilístico live. No es una recomendación de apuesta."
    : "";
  return `🔔 DIKAMAHA · ${scope}\n${subscription.rule_type}: ${value}${modelNote}`;
}

async function sendTelegram(chatId: number, text: string): Promise<void> {
  const cfg = config();
  let lastError: Error | undefined;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(`https://api.telegram.org/bot${cfg.botToken}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text }),
        signal: AbortSignal.timeout(15_000),
      });
      if (response.ok) return;
      lastError = new Error(`telegram_${response.status}`);
    } catch (error) {
      lastError = error instanceof Error ? error : new Error("telegram_request_failed");
    }
    await new Promise((resolve) => setTimeout(resolve, 500 * (2 ** attempt)));
  }
  throw lastError ?? new Error("telegram_request_failed");
}

async function cycle(sql: postgres.Sql): Promise<void> {
  const subscriptions = await sql<Subscription[]>`
    SELECT id, user_id, rule_type, fixture_id, league_slug, market_key,
           comparator, threshold, cooldown_seconds, last_observation,
           last_triggered_at
      FROM alert_subscriptions
     WHERE enabled = true
     ORDER BY created_at
     LIMIT 500
  `;
  if (!subscriptions.length) return;
  const leagueSet = new Set(
    subscriptions.map((row) => row.league_slug).filter(Boolean) as string[],
  );
  const catalogs = await Promise.allSettled([...leagueSet].map((league) => (
    dikamaha(`/v1/live?leagues=${encodeURIComponent(league)}&limit=20`)
  )));
  const liveFixtures = catalogs.flatMap((result) => (
    result.status === "fulfilled" ? records(result.value.fixtures) : []
  ));
  const fixtureIndex = new Map(liveFixtures.map((item) => [String(item.match_id), item]));
  const predictionCache = new Map<string, Json>();

  // `subscriptions` viene ordenado por `created_at` -la más antigua primero-,
  // hasta 500 por ciclo, de usuarios independientes entre sí. Cada una se
  // aísla con su propio try/catch: `dikamaha(...)` (predicción en vivo) y el
  // `UPDATE ... SET last_observation` no tenían protección propia -sólo el
  // envío a Telegram, más abajo, la tenía-, así que una suscripción cuya
  // predicción fallara de forma persistente cortaba el `for` y dejaba sin
  // evaluar a TODAS las suscripciones más nuevas detrás de ella, de
  // cualquier usuario, en cada ciclo, indefinidamente mientras el fallo
  // persistiera. Mismo patrón ya corregido en el canal de Telegram y en
  // Fase 123 (DEC-189/191).
  for (const subscription of subscriptions) {
    try {
      const fixture = subscription.fixture_id
        ? fixtureIndex.get(subscription.fixture_id) ?? null
        : liveFixtures.find((item) => item.league_slug === subscription.league_slug) ?? null;
      let prediction: Json | null = null;
      if (fixture && ["probability_delta", "market_threshold", "model_status"].includes(subscription.rule_type)) {
        const fixtureId = String(fixture.match_id);
        prediction = predictionCache.get(fixtureId) ?? await dikamaha("/v1/predict/live/fixture", {
          method: "POST",
          body: JSON.stringify({
            league_slug: fixture.league_slug,
            match_id: Number(fixture.match_id),
          }),
        });
        predictionCache.set(fixtureId, prediction);
      }
      const current = observation(subscription, fixture, prediction);
      const cooldownElapsed = !subscription.last_triggered_at
        || Date.now() - new Date(subscription.last_triggered_at).getTime()
          >= subscription.cooldown_seconds * 1000;
      const trigger = cooldownElapsed && shouldTrigger(subscription, current);
      await sql`
        UPDATE alert_subscriptions
           SET last_observation = ${sql.json(current)}, last_evaluated_at = now(),
               updated_at = now()
         WHERE id = ${subscription.id}
      `;
      if (!trigger) continue;
      const key = eventKey(subscription, current);
      const reserved = await sql<{ id: string }[]>`
        INSERT INTO alert_deliveries (subscription_id, event_key, status)
        VALUES (${subscription.id}, ${key}, 'pending')
        ON CONFLICT (subscription_id, event_key) DO NOTHING
        RETURNING id
      `;
      if (!reserved.length) continue;
      try {
        await sendTelegram(subscription.user_id, message(subscription, current));
        await sql.begin(async (transaction) => {
          await transaction`
            UPDATE alert_deliveries SET status = 'sent', delivered_at = now()
             WHERE id = ${reserved[0].id}
          `;
          await transaction`
            UPDATE alert_subscriptions SET last_triggered_at = now(), updated_at = now()
             WHERE id = ${subscription.id}
          `;
        });
      } catch (error) {
        const code = error instanceof Error ? error.message.slice(0, 80) : "telegram_failed";
        await sql`
          UPDATE alert_deliveries SET status = 'failed', error_code = ${code}, delivered_at = now()
           WHERE id = ${reserved[0].id}
        `;
      }
    } catch (error) {
      console.error(JSON.stringify({
        event: "telegram_alert_subscription_failed",
        subscription_id: subscription.id,
        fixture_id: subscription.fixture_id,
        league_slug: subscription.league_slug,
        code: error instanceof Error ? error.message.slice(0, 200) : "unknown",
      }));
    }
  }
}

export async function main(): Promise<void> {
  const cfg = config();
  const sql = postgres(cfg.databaseUrl, { max: 3, prepare: false });
  console.info(JSON.stringify({ event: "telegram_alert_worker_started", enabled: cfg.enabled }));
  try {
    for (;;) {
      const started = Date.now();
      if (cfg.enabled) {
        try {
          await cycle(sql);
          console.info(JSON.stringify({ event: "telegram_alert_cycle_ok", duration_ms: Date.now() - started }));
        } catch (error) {
          console.error(JSON.stringify({
            event: "telegram_alert_cycle_failed",
            code: error instanceof Error ? error.message.slice(0, 80) : "unknown",
          }));
        }
      }
      await new Promise((resolve) => setTimeout(resolve, cfg.pollSeconds * 1000));
    }
  } finally {
    await sql.end();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main();
}
