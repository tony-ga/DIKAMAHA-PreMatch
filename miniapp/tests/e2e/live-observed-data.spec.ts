import { expect, test, type Page } from "@playwright/test";

/**
 * Reporte real: "en cualquier partido in-live los datos del partido y
 * gráficas se están mostrando incorrectamente o no se están mostrando".
 *
 * Estas pruebas fijan las tres formas en que la pantalla mentía sin avisar:
 * un `0` que en realidad era "el proveedor no publica este dato", una curva
 * de presión plana en competiciones sin cronología por jugada (la limitación
 * que DEC-176 dejó abierta), y la tabla de periodos con nueve guiones cuando
 * el motor cae al contrato de compatibilidad.
 */

const LIVE_URL = "/live/401873624?league=uefa.super_cup";

async function stub(page: Page, payload: Record<string, unknown>) {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/javascript",
    body: `window.Telegram ||= { WebApp: { initData:'', colorScheme:'dark', ready(){}, expand(){}, close(){}, onEvent(){}, offEvent(){}, BackButton:{show(){},hide(){},onClick(){},offClick(){}} } };`,
  }));
  await page.route("**/api/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", count: 0, fixtures: [], favorites: [] }),
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "t" }),
  }));
  await page.route("**/api/predict/live", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(payload),
  }));
}

const FIXTURE = {
  home_team_name: "PSG", away_team_name: "Aston Villa",
  home_team_id: 160, away_team_id: 362,
  score_home: 1, score_away: 1,
  match_clock_seconds: 2700, provider_status_detail: "2do tiempo",
  source_fetched_at: "2026-08-13T20:00:00+00:00",
};

test("marca las métricas que el proveedor no publicó en vez de mostrarlas como cero", async ({ page }) => {
  await stub(page, {
    fixture: FIXTURE,
    observed_live_statistics: {
      source: "provider_boxscore_aggregate",
      // El boxscore llegó con tiros pero sin desglose ni córners: esas filas
      // no son un 0 observado, son un hueco del proveedor.
      unavailable_metrics: ["shots_on_target", "shots_off_target", "shots_blocked", "corners"],
      home: { goals: 1, shots: 9, shots_on_target: 0, corners: 0, fouls: 8, yellow_cards: 1 },
      away: { goals: 1, shots: 12, shots_on_target: 0, corners: 0, fouls: 11, yellow_cards: 2 },
    },
    recent_actions: [],
    match_dynamics: {
      current_minute: 45, points: [], goal_markers: [],
      pressure_granularity: "aggregate_only",
      weighted_event_count: 0, aggregate_action_count: 44,
    },
    official_live_prediction: {
      markets: { probability_home: 0.4, probability_draw: 0.3, probability_away: 0.3 },
      periods: {}, confidence: { classification: "media" },
      fallback: { applied: true, source: "Markov Live", reason: "engine_audit_failed" },
    },
    live_probability_engine: { audit: { passed: false } },
  });
  await page.goto(LIVE_URL);

  // El total real del proveedor se conserva...
  await expect(page.getByText("BOXSCORE OFICIAL")).toBeVisible();
  const shotsRow = page.locator(".live-stat-row").filter({ hasText: "Tiros" }).first();
  await expect(shotsRow).toContainText("9");
  // ...y las métricas ausentes se declaran en vez de fingir un cero.
  await expect(page.getByText("sin dato del proveedor").first()).toBeVisible();
  const cornersRow = page.locator(".live-stat-row").filter({ hasText: "Córners" });
  await expect(cornersRow).toContainText("—");
});

test("explica que la competición no publica cronología por jugada", async ({ page }) => {
  await stub(page, {
    fixture: FIXTURE,
    observed_live_statistics: {
      source: "provider_boxscore_aggregate", unavailable_metrics: [],
      home: { goals: 1, shots: 9 }, away: { goals: 1, shots: 12 },
    },
    recent_actions: [],
    match_dynamics: {
      current_minute: 45, points: [], goal_markers: [],
      pressure_granularity: "aggregate_only",
      weighted_event_count: 0, aggregate_action_count: 44,
    },
    official_live_prediction: { markets: {}, periods: {}, confidence: {}, fallback: {} },
    live_probability_engine: {},
  });
  await page.goto(LIVE_URL);

  await expect(page.getByText(/no publica la cronología por jugada/)).toBeVisible();
  await expect(page.getByText(/Todavía no hay acciones suficientes/)).toHaveCount(0);
});

test("sustituye la tabla de periodos vacía por la causa del fallback", async ({ page }) => {
  await stub(page, {
    fixture: FIXTURE,
    observed_live_statistics: { source: "provider_play_by_play", unavailable_metrics: [], home: {}, away: {} },
    recent_actions: [],
    match_dynamics: { current_minute: 45, points: [], goal_markers: [], pressure_granularity: "insufficient_events" },
    official_live_prediction: {
      markets: { probability_home: 0.4, probability_draw: 0.3, probability_away: 0.3 },
      periods: {}, confidence: { classification: "baja" },
      fallback: { applied: true, source: "Markov Live", reason: "engine_audit_failed" },
    },
    live_probability_engine: { audit: { passed: false } },
  });
  await page.goto(LIVE_URL);

  // El motivo va dentro de la propia nota que sustituye a la tabla, no sólo
  // en el aviso de fallback que ya existía aparte.
  await expect(page.getByText(/Sin desglose por periodo en este snapshot/))
    .toContainText("engine_audit_failed");
  await expect(page.locator(".live-period-table")).toHaveCount(0);
  // El indicador de confianza dejó de imprimir la literal "calculada".
  await expect(page.getByText("Confianza baja")).toBeVisible();
});
