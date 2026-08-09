import { expect, test } from "@playwright/test";

const emptyCatalog = { status: "ok", count: 0, fixtures: [] };

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
  await page.route("**/api/models", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok", models: [] }) }));
  await page.route("**/api/favorites**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ favorites: [] }) }));
});

test("keeps primary navigation available without returning home", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Lee el partido. Antes y durante." })).toBeVisible();
  await page.getByRole("link", { name: /En vivo/ }).click();
  await expect(page.getByRole("heading", { name: "Partidos en vivo" })).toBeVisible();
  await page.getByRole("link", { name: /Próximos/ }).click();
  await expect(page).toHaveURL(/\/upcoming$/);
  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();
});

test("applies Telegram light theme and renders the empty live state", async ({ page }) => {
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "",
        colorScheme: "light",
        ready() {}, expand() {}, close() {},
        onEvent() {}, offEvent() {},
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
      },
    };
  });
  await page.goto("/live");
  await expect(page.locator("html")).toHaveAttribute("data-tg-theme", "light");
  await expect(page.getByText("No hay partidos activos")).toBeVisible();
});

test("renders first half, second half and full-match pre-match markets", async ({ page }) => {
  await page.route("**/api/predict/upcoming", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      fixture: { home_team_name: "Cambridge United", away_team_name: "Barnet" },
      probability_home: 0.4,
      probability_draw: 0.3,
      probability_away: 0.3,
      probability_over_2_5: 0.55,
      probability_btts: 0.52,
      experimental_team_markets: {
        user_market_view: [
          { period: "first_half", team_side: "home", metric: "shots", line: 5.5, probability: 0.61 },
          { period: "second_half", team_side: "away", metric: "shots", line: 5.5, probability: 0.58 },
          { period: "full_match", team_side: "home", metric: "corners", line: 4.5, probability: 0.63 },
        ],
      },
    }),
  }));
  await page.goto("/predictions/401880614?league=eng.league_cup&home=351&away=280&kickoff=2030-01-10T20%3A00%3A00Z");
  await expect(page.getByRole("heading", { name: "Cambridge United vs Barnet" })).toBeVisible();
  await expect(page.getByText("Primer tiempo")).toBeVisible();
  await expect(page.getByText("Segundo tiempo")).toBeVisible();
  await expect(page.getByText("Partido completo")).toBeVisible();
});

test("renders the real live score, source timestamp and next event", async ({ page }) => {
  const markets = { probability_home: 0.62, probability_draw: 0.24, probability_away: 0.14, probability_over_2_5: 0.51, probability_btts: 0.44 };
  await page.route("**/api/predict/live", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      fixture: {
        home_team_name: "Real Madrid", away_team_name: "Barcelona",
        score_home: 2, score_away: 1, match_clock_seconds: 1920,
        provider_status_detail: "32'", source_fetched_at: "2026-08-08T20:32:00Z",
      },
      experimental_markov_live: {
        status: "experimental_shadow_not_promoted", markets,
        next_event: { horizon_minutes: 5, probabilities: { "home:goal": 0.12 }, probability_no_event: 0.82 },
      },
      experimental_hawkes_residual: { status: "experimental_shadow_not_promoted" },
      experimental_combined_live: { status: "experimental_shadow_not_promoted", markets },
      hawkes_league_admission: { admitted: true },
    }),
  }));
  await page.goto("/live/900001?league=esp.1");
  await expect(page.locator(".score b").nth(0)).toHaveText("2");
  await expect(page.locator(".score b").nth(1)).toHaveText("1");
  await expect(page.getByText(/PRÓXIMO EVENTO/)).toBeVisible();
  await expect(page.getByText(/32:00/)).toBeVisible();
  await expect(page.getByText(/actualizado/)).toBeVisible();
});
