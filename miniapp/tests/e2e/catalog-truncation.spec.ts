import { expect, test } from "@playwright/test";

/**
 * DEC-192: `/v1/upcoming` y `/v1/live` reparten su cupo con justicia entre
 * ligas (`allocate_fixtures_fairly`) y declaran `leagues_with_hidden_fixtures`
 * cuando el cupo no alcanzó para mostrar todo lo que encontraron. Antes el
 * recorte era invisible: el usuario simplemente no veía partidos de una liga
 * sin ninguna pista de que existían más de los que caben en pantalla.
 */

async function stubBase(page: import("@playwright/test").Page) {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/javascript",
    body: `window.Telegram ||= { WebApp: { initData:'', colorScheme:'dark', ready(){}, expand(){}, close(){}, onEvent(){}, offEvent(){}, BackButton:{show(){},hide(){},onClick(){},offClick(){}} } };`,
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "t" }),
  }));
  await page.route("**/api/favorites**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({ favorites: [] }),
  }));
  await page.route("**/api/explorer/leagues", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ leagues: [{ slug: "mex.1", name: "Liga MX" }], count: 1 }),
  }));
  await page.route("**/api/explorer/dates**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ dates: [{ date: "20260814", label: "14/08" }], count: 1 }),
  }));
}

const FIXTURE = {
  match_id: 1, league_slug: "mex.1", home_team_id: 10, away_team_id: 20,
  home_team_name: "Cruz Azul", away_team_name: "Pumas",
  kickoff_ts: "2026-08-14T20:00:00Z",
};

test("shows the truncation notice on Próximos when leagues were left out", async ({ page }) => {
  await stubBase(page);
  await page.route("**/api/upcoming**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      status: "ok", count: 1, fixtures: [FIXTURE], league_count: 63, date_count: 14,
      truncated: true, leagues_with_hidden_fixtures: ["uefa.europa.conf_qual"],
    }),
  }));

  await page.goto("/upcoming");

  await expect(page.getByText(/Hay más partidos de los que caben aquí/)).toBeVisible();
  await expect(page.getByText("uefa.europa.conf_qual")).toBeVisible();
});

test("stays silent on Próximos when nothing was truncated", async ({ page }) => {
  await stubBase(page);
  await page.route("**/api/upcoming**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      status: "ok", count: 1, fixtures: [FIXTURE], league_count: 1, date_count: 14,
      truncated: false, leagues_with_hidden_fixtures: [],
    }),
  }));

  await page.goto("/upcoming");

  await expect(page.getByText(FIXTURE.home_team_name)).toBeVisible();
  await expect(page.getByText(/Hay más partidos de los que caben aquí/)).toHaveCount(0);
});

test("shows the truncation notice on the live catalog too", async ({ page }) => {
  await stubBase(page);
  await page.route("**/api/live**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      status: "ok", count: 1, fixtures: [FIXTURE], league_count: 63, date_count: 3,
      truncated: true, leagues_with_hidden_fixtures: ["ksa.1", "jpn.1"],
    }),
  }));

  await page.goto("/live");

  await expect(page.getByText(/Hay más partidos de los que caben aquí/)).toBeVisible();
  await expect(page.getByText("ksa.1, jpn.1")).toBeVisible();
});
