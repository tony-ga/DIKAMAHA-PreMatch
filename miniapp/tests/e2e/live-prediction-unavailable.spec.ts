import { expect, test, type Page } from "@playwright/test";

/**
 * Mismo contrato que prediction-unavailable.spec.ts, para la vista live.
 *
 * Reporte real: Paris Saint-Germain vs Aston Villa (Supercopa de Europa)
 * estaba en vivo y la predicción no cargaba. La razón era legítima
 * (`uefa.super_cup` tiene un único partido en el snapshot), pero
 * `live-detail.tsx` mostraba un mensaje genérico de "snapshot rechazado o
 * partido ya no activo" que no distinguía nada -a diferencia de la vista
 * pre-match, que ya traducía este código a una explicación concreta.
 */

async function stub(page: Page, reason: string, status = 422) {
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
    status, contentType: "application/json",
    body: JSON.stringify({ error: reason }),
  }));
}

const URL = "/live/401873624?league=uefa.super_cup";

test("explains that the live competition lacks causal history", async ({ page }) => {
  await stub(page, "league_history_below_minimum");
  await page.goto(URL);

  await expect(page.getByRole("heading", {
    name: "Sin historial suficiente en esta competición",
  })).toBeVisible();
  await expect(page.getByText(/finales a partido único/)).toBeVisible();
  await expect(page.getByText(/No es un fallo del servicio/)).toBeVisible();
});

test("distinguishes a fixture that is no longer live", async ({ page }) => {
  await stub(page, "live_fixture_not_active");
  await page.goto(URL);

  await expect(page.getByRole("heading", {
    name: "El partido ya no está en vivo",
  })).toBeVisible();
});
