import { expect, test, type Page } from "@playwright/test";

/**
 * DEC-192: el worker de alertas (`miniapp/worker/alerts.ts`) sólo evalúa
 * fixtures que aparecen en su propio barrido de `/v1/live`/`/v1/upcoming`
 * por liga. Antes el formulario de alta aceptaba cualquier `fixtureId`
 * escrito a mano sin comprobarlo contra ninguno de los dos catálogos, así
 * que un ID mal copiado o de un partido ya terminado se guardaba igual y
 * nunca se evaluaba, sin ningún error visible en el momento de crearlo.
 */

const emptyCatalog = { status: "ok", count: 0, fixtures: [] };

async function stub(page: Page) {
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
  await page.route("**/api/subscriptions", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ subscriptions: [] }),
      });
    }
    return route.fulfill({
      status: 201, contentType: "application/json",
      body: JSON.stringify({ subscription: { id: "sub-1" } }),
    });
  });
}

test("rejects an unknown fixture id before saving the subscription", async ({ page }) => {
  await stub(page);
  await page.route("**/api/upcoming**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
  }));
  await page.route("**/api/live**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
  }));
  let posted = false;
  page.on("request", (request) => {
    if (request.url().includes("/api/subscriptions") && request.method() === "POST") posted = true;
  });

  await page.goto("/subscriptions");
  await page.fill("#fixtureId", "999999999");
  await page.fill("#leagueSlug", "eng.2");
  await page.click("button[type=submit]");

  await expect(page.getByText(/No encontramos el partido 999999999 en eng.2/)).toBeVisible();
  expect(posted).toBe(false);
});

test("accepts a fixture id found in the upcoming catalog", async ({ page }) => {
  await stub(page);
  await page.route("**/api/upcoming**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      status: "ok", count: 1,
      fixtures: [{ match_id: 401880614, league_slug: "eng.2", home_team_name: "A", away_team_name: "B", kickoff_ts: "2026-08-20T20:00:00Z" }],
    }),
  }));
  await page.route("**/api/live**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
  }));
  let posted = false;
  page.on("request", (request) => {
    if (request.url().includes("/api/subscriptions") && request.method() === "POST") posted = true;
  });

  await page.goto("/subscriptions");
  await page.fill("#fixtureId", "401880614");
  await page.fill("#leagueSlug", "eng.2");
  await page.click("button[type=submit]");

  await expect(page.getByText(/No encontramos el partido/)).toHaveCount(0);
  await expect.poll(() => posted).toBe(true);
});

test("accepts a fixture id found only in the live catalog", async ({ page }) => {
  await stub(page);
  await page.route("**/api/upcoming**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
  }));
  await page.route("**/api/live**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      status: "ok", count: 1,
      fixtures: [{ match_id: 401880614, league_slug: "eng.2", home_team_name: "A", away_team_name: "B", kickoff_ts: "2026-08-20T20:00:00Z" }],
    }),
  }));
  let posted = false;
  page.on("request", (request) => {
    if (request.url().includes("/api/subscriptions") && request.method() === "POST") posted = true;
  });

  await page.goto("/subscriptions");
  await page.fill("#fixtureId", "401880614");
  await page.fill("#leagueSlug", "eng.2");
  await page.click("button[type=submit]");

  await expect.poll(() => posted).toBe(true);
});

test("skips fixture validation for a league-wide subscription", async ({ page }) => {
  await stub(page);
  let upcomingCalled = false;
  await page.route("**/api/upcoming**", (route) => {
    upcomingCalled = true;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog) });
  });
  await page.route("**/api/live**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
  }));
  let posted = false;
  page.on("request", (request) => {
    if (request.url().includes("/api/subscriptions") && request.method() === "POST") posted = true;
  });

  await page.goto("/subscriptions");
  await page.fill("#leagueSlug", "eng.2");
  await page.click("button[type=submit]");

  await expect.poll(() => posted).toBe(true);
  expect(upcomingCalled).toBe(false);
});
