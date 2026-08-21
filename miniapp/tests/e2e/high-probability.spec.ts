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
    selection: "target_band",
    bucket: [0.6, 0.85],
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
  await expect(page.getByText("Real Betis", { exact: true })).toBeVisible();
  await expect(page.getByText("Córners de Real Betis · Más de 4.5")).toBeVisible();
  // La cifra grande es la tasa observada (89%), no la del modelo (68%).
  await expect(page.getByText("89%", { exact: true })).toBeVisible();
  await expect(page.getByText(/Ventaja del modelo/)).toBeVisible();
  await expect(page.getByText(/declara 68%/)).toBeVisible();
  await expect(page.getByText("SHADOW").first()).toBeVisible();
});

test("groups every market of a match under its own fixture card", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok",
      picks: [
        pick({ market: "home_corners", metric: "corners", line: 4.5, observed_rate: 0.71 }),
        pick({
          market: "away_shots_first_half", metric: "shots", team_side: "away",
          period: "first_half", line: 5.5, observed_rate: 0.66,
          edge_source: "base_rate_driven",
        }),
        pick({
          market: "home_yellow_cards", metric: "yellow_cards", line: 1.5,
          observed_rate: 0.63,
        }),
      ],
      count: 3,
      fixtures_with_picks: 1,
      fixtures_scanned: 4,
      provenance: { eligible_cells: 9 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  // Las tres líneas de un mismo partido aparecen juntas -no sólo la más fuerte-.
  await expect(page.getByText("Córners de Real Betis · Más de 4.5")).toBeVisible();
  await expect(page.getByText("Tarjetas de Real Betis · Más de 1.5")).toBeVisible();
  await expect(page.getByText("Primer tiempo")).toBeVisible();
  await expect(page.getByText("Tiros de Sevilla · Más de 5.5")).toBeVisible();
  await expect(page.getByText("Partido completo")).toBeVisible();
  await expect(page.getByText(/Ventaja de la tasa base/)).toBeVisible();
});

test("declares a pick recovered outside the target band", async ({ page }) => {
  // Nivel 2 de la selección (DEC-187): el mercado no tenía ninguna línea en
  // `[0.60, 0.85]` y se publica la más cercana dentro de `[0.55, 0.90]` en vez
  // de dejarlo vacío. Debe verse que su evidencia no es la del nivel 1.
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok",
      picks: [pick({
        market: "home_yellow_cards_first_half", metric: "yellow_cards",
        period: "first_half", line: 0.5, direction: "over",
        model_probability: 0.58, observed_rate: 0.58,
        edge_source: "base_rate_driven",
        selection: "outside_band", bucket: [0.55, 0.9],
      })],
      count: 1, fixtures_scanned: 6,
      provenance: { eligible_cells: 9 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByText("Tarjetas de Real Betis · Más de 0.5")).toBeVisible();
  await expect(page.getByText(/fuera de la banda objetivo/)).toBeVisible();
});

test("does not label a pick selected inside the target band", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok", picks: [pick()], count: 1, fixtures_scanned: 6,
      provenance: { eligible_cells: 9 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByText("Córners de Real Betis · Más de 4.5")).toBeVisible();
  await expect(page.getByText(/fuera de la banda objetivo/)).toHaveCount(0);
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
    name: "Hoy no hay ningún pick disponible",
  })).toBeVisible();
  await expect(page.getByText(/preferimos no mostrar nada/)).toBeVisible();
});

test("explains unavailable sources instead of failing silently", async ({ page }) => {
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "unavailable",
      reason: "high_probability_sources_unavailable",
      picks: [], count: 0, fixtures_scanned: 0,
      provenance: {
        status: "unavailable", eligible_cells: 0,
        goal_markets_gate_available: false, team_markets_sha256: "",
      },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByRole("heading", { name: "Fuentes no disponibles" })).toBeVisible();
});

test("publishes the historical rate of the direction actually picked", async ({ page }) => {
  // Un pick `under` muestra la tasa de que ocurra el under. El defecto de
  // producción (DEC-182) publicaba la tasa del `over` también para picks
  // `under`, produciendo "menos de 0.5 córners: 96%" cuando lo real era 3.8%.
  await page.route("**/api/high-probability**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok",
      picks: [pick({
        market: "away_corners", metric: "corners", team_side: "away",
        line: 8.5, direction: "under", observed_rate: 0.65,
        observed_ci95: [0.61, 0.69], model_probability: 0.67,
      })],
      count: 1,
      fixtures_scanned: 3,
      provenance: { eligible_cells: 9 },
    }),
  }));

  await page.goto("/mayor-probabilidad");
  await expect(page.getByText("Córners de Sevilla · Menos de 8.5")).toBeVisible();
  await expect(page.getByText("65%", { exact: true })).toBeVisible();
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

// Cada entrada nueva estrecha la barra inferior. La cuenta se afirma para que
// añadir un destino obligue a volver aquí y comprobar que sigue cabiendo.
// 375 px es el ancho mínimo realista en Telegram y donde el texto se rompe
// primero, así que la regresión se vigila ahí y no en el viewport por defecto.
test.describe("bottom navigation at the narrowest realistic width", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("fits every destination without clipping or horizontal overflow", async ({ page }) => {
    await page.route("**/api/high-probability**", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", picks: [], count: 0, fixtures_scanned: 0, provenance: { eligible_cells: 9 } }),
    }));

    await page.goto("/mayor-probabilidad");
    const nav = page.getByRole("navigation", { name: "Navegación principal" });
    await expect(nav.locator("a")).toHaveCount(8);

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
