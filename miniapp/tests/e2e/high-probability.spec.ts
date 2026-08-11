import { expect, test } from "@playwright/test";

const emptyCatalog = { status: "ok", count: 0, fixtures: [] };

function pick(overrides: Record<string, unknown> = {}) {
  return {
    market: "home_corners_over_4_5",
    direction: "over",
    metric: "corners",
    team_side: "home",
    period: "full_match",
    line: 4.5,
    confidence: 0.7,
    model_probability: 0.683,
    observed_rate: 0.893,
    observed_ci95: [0.833, 0.933],
    sample_size: 149,
    edge_source: "model_edge",
    skill_vs_naive: 0.248,
    bucket: [0.65, 0.75],
    league_stability: 1.0,
    status: "experimental_shadow_not_promoted",
    fixture: {
      match_id: 401,
      league_slug: "esp.1",
      kickoff_ts: "2026-08-11T20:00:00Z",
      home_team_id: 10,
      away_team_id: 20,
      home_team_name: "Real Betis",
      away_team_name: "Sevilla",
      home_team_logo: null,
      away_team_logo: null,
    },
    ...overrides,
  };
}

test.beforeEach(async ({ page }) => {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/javascript",
    body: `window.Telegram ||= { WebApp: {
      initData: '', colorScheme: 'dark', ready(){}, expand(){}, close(){},
      onEvent(){}, offEvent(){}, BackButton: { show(){}, hide(){}, onClick(){}, offClick(){} }
    }};`,
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "csrf-test" }),
  }));
  await page.route("**/api/live**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog) }));
  await page.route("**/api/upcoming**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog) }));
  await page.route("**/api/favorites**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ favorites: [] }) }));
});

test("shows the observed hit rate and flags a model edge", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok",
      classification: "experimental_shadow_not_promoted",
      picks: [pick()],
      count: 1,
      total_candidates: 1,
      fixtures_scanned: 6,
      fixtures_without_prediction: 0,
      provenance: { status: "experimental_shadow_not_promoted", eligible_cells: 9 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByRole("heading", { name: "Mayor probabilidad" })).toBeVisible();
  await expect(page.getByText("Córners de Real Betis · Más de 4.5")).toBeVisible();
  // La cifra grande es la tasa observada (89%), no la del modelo (68%).
  await expect(page.getByText("89%", { exact: true })).toBeVisible();
  await expect(page.getByText("149 picks")).toBeVisible();
  await expect(page.getByText("Ventaja del modelo")).toBeVisible();
  await expect(page.getByText("SHADOW").first()).toBeVisible();
});

test("says plainly when the edge comes from the base rate", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok",
      picks: [pick({
        market: "home_shots_second_half_over_5_5",
        metric: "shots",
        period: "second_half",
        line: 5.5,
        observed_rate: 0.811,
        edge_source: "base_rate_driven",
        skill_vs_naive: 0.0,
      })],
      count: 1,
      fixtures_scanned: 4,
      provenance: { eligible_cells: 9 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByText("Tiros de Real Betis · 2T · Más de 5.5")).toBeVisible();
  await expect(page.getByText("Ventaja de la tasa base")).toBeVisible();
  await expect(page.getByText(/el modelo no añade ventaja demostrada/)).toBeVisible();
});

test("prefers an honest empty state over a weak pick", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok", picks: [], count: 0, fixtures_scanned: 11,
      provenance: { eligible_cells: 9 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByRole("heading", {
    name: "Hoy no hay ningún pick que supere el gate",
  })).toBeVisible();
  await expect(page.getByText(/preferimos no mostrar nada/)).toBeVisible();
});

test("explains an unavailable gate instead of failing silently", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "unavailable",
      reason: "phase122_eligibility_unavailable",
      picks: [], count: 0, fixtures_scanned: 0,
      provenance: { status: "unavailable", eligible_cells: 0 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByRole("heading", { name: "Gate no disponible" })).toBeVisible();
});

test("is reachable from the primary navigation", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", picks: [], count: 0, fixtures_scanned: 0, provenance: { eligible_cells: 9 } }),
  }));

  await page.goto("/");
  await page.getByRole("link", { name: /Mayor prob/ }).click();
  await expect(page).toHaveURL(/\/mayor-probabilidad$/);
  await expect(page.getByRole("heading", { name: "Mayor probabilidad" })).toBeVisible();
});

// La sexta entrada obligó a pasar la barra inferior de cinco a seis columnas.
// 375 px es el ancho mínimo realista en Telegram y donde el texto se rompe
// primero, así que la regresión se vigila ahí y no en el viewport por defecto.
test.describe("bottom navigation at the narrowest realistic width", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("fits six destinations without clipping or horizontal overflow", async ({ page }) => {
    await page.route("**/api/high-probability**", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", picks: [], count: 0, fixtures_scanned: 0, provenance: { eligible_cells: 9 } }),
    }));

    await page.goto("/mayor-probabilidad");
    const nav = page.getByRole("navigation", { name: "Navegación principal" });
    await expect(nav.locator("a")).toHaveCount(6);

    const layout = await nav.evaluate((element) => {
      const bar = element as HTMLElement;
      const labels = [...bar.querySelectorAll("small")] as HTMLElement[];
      return {
        barOverflow: bar.scrollWidth - bar.clientWidth,
        clipped: labels
          .filter((label) => label.scrollWidth > label.clientWidth + 1)
          .map((label) => label.textContent),
        bodyOverflow: document.body.scrollWidth - document.body.clientWidth,
      };
    });
    expect(layout.clipped).toEqual([]);
    expect(layout.barOverflow).toBeLessThanOrEqual(0);
    expect(layout.bodyOverflow).toBeLessThanOrEqual(0);
  });
});
