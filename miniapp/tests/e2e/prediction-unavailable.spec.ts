import { expect, test, type Page } from "@playwright/test";

/**
 * Un fixture sin historial causal debe explicarse, no quedarse en blanco.
 *
 * Reporte real: al abrir Paris Saint-Germain vs Aston Villa (Supercopa de
 * Europa) no aparecía ninguna predicción. La causa es legítima —
 * `uefa.super_cup` tiene un único partido en el snapshot y el motor exige ocho
 * de la misma competición— pero la interfaz mostraba un mensaje genérico que
 * mezclaba historia, identidad y cutoff, así que el usuario no podía saber si
 * el problema era del partido o del servicio.
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
  await page.route("**/api/predict/upcoming", (route) => route.fulfill({
    status, contentType: "application/json",
    body: JSON.stringify({ error: reason }),
  }));
}

const URL =
  "/predictions/401903100?league=uefa.super_cup&home=160&away=362" +
  "&homeName=Paris%20Saint-Germain&awayName=Aston%20Villa" +
  "&kickoff=2026-08-13T19%3A00%3A00Z";

test("explains that the competition lacks causal history", async ({ page }) => {
  await stub(page, "league_history_below_minimum");
  await page.goto(URL);

  await expect(page.getByRole("heading", {
    name: "Sin historial suficiente en esta competición",
  })).toBeVisible();
  await expect(page.getByText(/finales a partido único/)).toBeVisible();
  await expect(page.getByText(/No es un fallo del servicio/)).toBeVisible();
});

test("distinguishes an unsupported league", async ({ page }) => {
  await stub(page, "unsupported_league");
  await page.goto(URL);

  await expect(page.getByRole("heading", {
    name: "Competición no soportada",
  })).toBeVisible();
});

test("distinguishes a service outage from a data limitation", async ({ page }) => {
  await stub(page, "upstream_unavailable", 503);
  await page.goto(URL);

  await expect(page.getByRole("heading", {
    name: "Servicio no disponible",
  })).toBeVisible();
  await expect(page.getByText(/Vuelve a intentarlo/)).toBeVisible();
});
