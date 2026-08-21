import { expect, test } from "@playwright/test";

const emptyCatalog = { status: "ok", count: 0, fixtures: [] };

function leg(overrides: Record<string, unknown> = {}) {
  return {
    key: "home_corners_over_4_5",
    metric: "corners",
    team_side: "home",
    period: "full_match",
    line: 4.5,
    direction: "over",
    probability: 0.72,
    baseline_probability: 0.55,
    threshold: 0.6,
    source_model: "phase84a_team_count",
    status: "experimental_shadow_not_promoted",
    ...overrides,
  };
}

function match(index: number, legs: Record<string, unknown>[]) {
  return {
    match_id: 400 + index,
    league_slug: "esp.1",
    fixture_key: `esp.1:${400 + index}`,
    kickoff_ts: "2026-08-22T20:00:00Z",
    home_team_id: 10 + index,
    away_team_id: 20 + index,
    home_team_name: index === 0 ? "Real Betis" : "Valencia",
    away_team_name: index === 0 ? "Sevilla" : "Celta",
    home_team_logo: null,
    away_team_logo: null,
    legs,
  };
}

const MENU = {
  status: "ok",
  classification: "experimental_shadow_not_promoted",
  matches: [
    match(0, [leg(), leg({ key: "away_shots_over_10_5", metric: "shots",
                           team_side: "away", line: 10.5, probability: 0.68 })]),
    match(1, [leg({ probability: 0.75 })]),
  ],
  legs: 3,
  matches_with_legs: 2,
  min_legs: 2,
  max_legs: 5,
  max_legs_per_match: 1,
  criteria_version: "phase135_parlay_eligibility_v1",
  criteria_sha256: "a".repeat(64),
  delivery: { "2": 0.97, "3": 0.94 },
  fixtures_scanned: 6,
  fixtures_catalog_size: 6,
  fixtures_without_prediction: 0,
  disclosure: "Menú experimental sin validación prospectiva.",
};

test.beforeEach(async ({ page }) => {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/javascript",
    body: `window.Telegram ||= { WebApp: {
      initData: 'stub', colorScheme: 'dark', ready(){}, expand(){}, close(){},
      onEvent(){}, offEvent(){}, BackButton: { show(){}, hide(){}, onClick(){}, offClick(){} }
    }};`,
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      user: { id: 42, firstName: "Marco" }, csrfToken: "csrf-test",
      entitlement: { plan: "premium" },
    }),
  }));
  for (const path of ["**/api/live**", "**/api/upcoming**"]) {
    await page.route(path, (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify(emptyCatalog),
    }));
  }
  await page.route("**/api/favorites**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ favorites: [] }),
  }));
  await page.route("**/api/parlay/menu**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(MENU),
  }));
});

test("lists eligible legs and states the rule that excludes higher numbers", async ({ page }) => {
  await page.goto("/parlay");
  await expect(page.getByRole("heading", { name: "Constructor de Parlays" })).toBeVisible();
  await expect(page.getByText("Córners de Real Betis · Más de 4.5")).toBeVisible();
  await expect(page.getByText("Tiros de Sevilla · Más de 10.5")).toBeVisible();
  // El aviso debe decir explícitamente por qué no se ordena por probabilidad.
  await expect(page.getByText(/declara 88% y cumple 51%/)).toBeVisible();
  await expect(page.getByText("SHADOW").first()).toBeVisible();
});

test("needs two legs before showing a joint probability", async ({ page }) => {
  await page.goto("/parlay");
  await page.getByRole("button", { name: "Añadir al parlay" }).first().click();
  await expect(page.getByText(/Añade al menos 2 piernas/)).toBeVisible();
  await expect(page.getByText("Probabilidad conjunta", { exact: true })).toHaveCount(0);
});

test("a second leg from the same match replaces the first", async ({ page }) => {
  await page.goto("/parlay");
  const buttons = page.getByRole("button", { name: "Añadir al parlay" });
  await buttons.nth(0).click();   // Betis · córners
  await buttons.nth(0).click();   // Sevilla · tiros, mismo partido
  // Sustituye en vez de acumular: sigue habiendo una sola pierna.
  await expect(page.getByText("1/5")).toBeVisible();
});

test("quotes the parlay through the server and shows the delivery ratio", async ({ page }) => {
  await page.route("**/api/parlay/quote", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "experimental_shadow_not_promoted",
      legs: 2,
      joint_probability: 0.54,
      delivery_ratio: 0.97,
      expected_delivery: 0.5238,
      criteria_version: "phase135_parlay_eligibility_v1",
    }),
  }));
  await page.goto("/parlay");
  await page.getByRole("button", { name: "Añadir al parlay" }).nth(0).click();
  await page.getByRole("button", { name: "Añadir al parlay" }).last().click();
  await expect(page.getByText("Probabilidad conjunta", { exact: true })).toBeVisible();
  // La cifra mostrada es la del servidor (54%), no un cálculo local distinto.
  await expect(page.getByText("54%", { exact: true })).toBeVisible();
  await expect(page.getByText("Ajustada por entrega")).toBeVisible();
  await expect(page.getByText(/se cumple el 97%/)).toBeVisible();
});

test("degrades to an explicit panel when the gate is unavailable", async ({ page }) => {
  await page.route("**/api/parlay/menu**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "unavailable", reason: "parlay_criteria_unavailable",
      matches: [], legs: 0,
    }),
  }));
  await page.goto("/parlay");
  await expect(page.getByText("Criterios no disponibles")).toBeVisible();
});
