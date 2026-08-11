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
  await page.route("**/api/provider/predictor**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "not_published", probabilities: null, history: [], market_context: { status: "not_published" } }) }));
  await page.route("**/api/provider/markets**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ source_name: "External", status: "not_published", count: 0, fixtures: [] }) }));
  await page.route("**/api/favorites**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ favorites: [] }) }));
  await page.route("**/api/readiness", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ready: true, contract_version: "phase_6_1", service_version: "1.6.0" }) }));
  await page.route("**/api/explorer/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const payloads: Record<string, unknown> = {
      "/api/explorer/leagues": { leagues: [{ slug: "mex.1", name: "Liga MX" }], count: 1 },
      "/api/explorer/dates": { dates: [{ date: "20260808", label: "08/08" }], count: 1 },
      "/api/explorer/fixtures": { fixtures: [{ match_id: 7001, competition_id: "7001", league_slug: "mex.1", home_team_id: 10, away_team_id: 20, home_team_name: "Cruz Azul", away_team_name: "Pumas", kickoff_ts: "2026-08-08T20:00:00Z", home_score: 2, away_score: 1, status_detail: "Final" }], count: 1 },
      "/api/explorer/fixture/context": { status: "available", fixture: { kickoff_ts: "2026-08-08T20:00:00Z" }, competition: { name: "Liga MX", phase: "Apertura" }, venue: { name: "Estadio Central", city: "CDMX" }, teams: { home: { name: "Cruz Azul" }, away: { name: "Pumas" } }, officials: [{ name: "Árbitro Uno" }], broadcasts: [{ name: "ESPN" }], team_context: {}, availability: {} },
      "/api/explorer/match/plays": { plays: [{ id: "p1", type: "goal", label: "Gol", clock: "42'", period: 1, text: "Gol de Cruz Azul" }], count: 1, raw_count: 1 },
      "/api/explorer/match/statistics": { teams: { home: { name: "Cruz Azul", abbreviation: "CAZ" }, away: { name: "Pumas", abbreviation: "PUM" } }, periods: { home: { first_half: { goals: 1 }, second_half: { goals: 1 }, total: { goals: 2, shots: 9 } }, away: { first_half: { goals: 0 }, second_half: { goals: 1 }, total: { goals: 1, shots: 7 } } }, boxscore: [], reconciled: true, score_reconciled: true },
      "/api/explorer/teams": { teams: [{ id: "10", name: "Cruz Azul", abbreviation: "CAZ" }], count: 1 },
      "/api/explorer/team/roster": { team: { id: "10", name: "Cruz Azul" }, players: [{ id: "99", name: "Jugador Azul", jersey: "9", position: "Delantero", age: 24 }] },
      "/api/explorer/player": { id: "99", name: "Jugador Azul", position: "Delantero", age: 24, height: "1.80 m", weight: "75 kg", citizenship: "México", active: true, team: { name: "Cruz Azul" }, statistics: [{ name: "appearances", value: "12" }, { name: "totalGoals", value: "7" }] },
    };
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payloads[path] ?? {}) });
  });
});

test("keeps primary navigation available without returning home", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Lee el partido. Antes y durante." })).toBeVisible();
  await page.getByRole("link", { name: /En vivo/ }).click();
  await expect(page.getByRole("heading", { name: "Partidos en vivo" })).toBeVisible();
  await page.getByRole("link", { name: /Predicciones/ }).click();
  await expect(page).toHaveURL(/\/predictions$/);
  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();
});

test("confirms the Telegram session before loading protected catalogs", async ({ page }) => {
  await page.unroute("**/api/session/me");
  const requests: string[] = [];
  let sessionChecks = 0;
  await page.route("**/api/session/me", (route) => {
    requests.push("session/me");
    sessionChecks += 1;
    if (sessionChecks === 1) {
      return route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ error: "authentication_required" }) });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "csrf-test" }),
    });
  });
  await page.route("**/api/session/telegram", (route) => {
    requests.push("session/telegram");
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "csrf-test" }),
    });
  });
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "query_id=test&auth_date=1786250000&hash=test",
        colorScheme: "dark",
        ready() {}, expand() {}, close() {},
        onEvent() {}, offEvent() {},
        BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
      },
    };
  });

  await page.goto("/upcoming");
  await expect(page.getByLabel("Liga").locator("option")).toHaveCount(2);
  const loginIndex = requests.indexOf("session/telegram");
  const confirmationIndex = requests.indexOf("session/me", loginIndex + 1);
  expect(loginIndex).toBeGreaterThanOrEqual(0);
  expect(confirmationIndex).toBeGreaterThan(loginIndex);
});

test("explains a failed catalog and restores league selection on retry", async ({ page }) => {
  let failuresRemaining = 2;
  await page.route("**/api/explorer/leagues", (route) => {
    if (failuresRemaining > 0) {
      failuresRemaining -= 1;
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "upstream_unavailable" }) });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ leagues: [{ slug: "bra.1", name: "Brasileirão" }], count: 1 }) });
  });

  await page.goto("/upcoming");
  await expect(page.getByRole("alert").filter({ hasText: "El catálogo no respondió" })).toBeVisible();
  await page.getByRole("button", { name: "Reintentar catálogos" }).click();
  await expect(page.getByLabel("Liga").locator("option")).toHaveCount(2);
  await page.getByLabel("Liga").selectOption("bra.1");
  await expect(page.getByLabel("Liga")).toHaveValue("bra.1");
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
  await page.unroute("**/api/provider/predictor**");
  await page.route("**/api/provider/predictor**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "available", probabilities: { home: .46, draw: .29, away: .25 }, history: [], market_context: { status: "financial_isolated_available", providers: [{ provider_id: "100", provider_name: "Provider", details: "CAM +130", markets: { moneyline: { home: { open: { odds: "+130" }, close: { odds: "+125" } }, draw: { close: { odds: "+210" } }, away: { live: { odds: "+240" } } } } }] } }),
  }));
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
      expected_home_goals: 1.42,
      expected_away_goals: 1.08,
      lambda_home: 1.42,
      lambda_away: 1.08,
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
  await expect(page.getByText("Goles esperados por equipo")).toBeVisible();
  await expect(page.getByText("Comparativa matemática del partido")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Predictor del proveedor" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Movimiento del mercado" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "+130" })).toBeVisible();
});

test("renders an adaptive market grid with distinct lines per period", async ({ page }) => {
  const ladder = (line: number, over: number, base: number) => ({
    line, over_probability: over, under_probability: 1 - over,
    baseline_over_probability: base, baseline_under_probability: 1 - base,
  });
  await page.route("**/api/predict/upcoming", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      fixture: { home_team_name: "Cambridge United", away_team_name: "Barnet" },
      probability_home: 0.4, probability_draw: 0.3, probability_away: 0.3,
      probability_over_2_5: 0.55, probability_btts: 0.52,
      expected_home_goals: 1.42, expected_away_goals: 1.08,
      lambda_home: 1.42, lambda_away: 1.08,
      experimental_team_markets: {
        user_market_view: [
          { period: "full_match", team_side: "home", metric: "corners", line: 4.5, probability: 0.63 },
        ],
        bounded_market_grid_view: [
          {
            key: "home_corners_first_half", metric: "corners", team_side: "home",
            period: "first_half", expected_count: 2.6, most_likely_count: 2,
            lines: [ladder(1.5, 0.71, 0.66), ladder(2.5, 0.48, 0.44), ladder(3.5, 0.27, 0.25)],
            status: "experimental_shadow_not_promoted",
          },
          {
            key: "home_corners_second_half", metric: "corners", team_side: "home",
            period: "second_half", expected_count: 3.4, most_likely_count: 3,
            lines: [ladder(2.5, 0.69, 0.62), ladder(3.5, 0.46, 0.41), ladder(4.5, 0.25, 0.23)],
            status: "experimental_shadow_not_promoted",
          },
        ],
      },
    }),
  }));
  await page.goto("/predictions/401880614?league=eng.league_cup&home=351&away=280&kickoff=2030-01-10T20%3A00%3A00Z");
  const grid = page.locator("article.model-card").filter({ hasText: "Rejilla adaptativa por periodo" });
  await expect(grid).toBeVisible();
  await expect(grid.getByText("μ 2.6")).toBeVisible();
  await expect(grid.getByText("μ 3.4")).toBeVisible();
  await expect(grid.getByText("Más de 1.5", { exact: false })).toBeVisible();
  await expect(grid.getByText("Más de 4.5", { exact: false })).toBeVisible();
  await expect(grid.getByText("vs baseline +7.0 pp", { exact: false })).toBeVisible();
});

test("shows the global open close and live market tape", async ({ page }) => {
  await page.unroute("**/api/provider/markets**");
  await page.route("**/api/provider/markets**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      source_name: "External", status: "available", count: 1,
      fixtures: [{ event_id: "401", home_team: { name: "América" }, away_team: { name: "Nacional" }, market_context: { providers: [{ provider_id: "100", provider_name: "Provider", details: "AME +140", markets: { moneyline: { home: { open: { odds: "+130" }, close: { odds: "+140" }, live: { odds: "-210" } }, draw: { open: { odds: "+200" }, close: { odds: "+205" }, live: { odds: "+240" } }, away: { open: { odds: "+195" }, close: { odds: "+200" }, live: { odds: "+850" } } } } }] } }],
    }),
  }));

  await page.goto("/markets");
  await expect(page.getByRole("heading", { name: "Pronósticos globales" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "América" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "-210" })).toBeVisible();
  await expect(page.getByText(/No es SPI/)).toBeVisible();
});

test("recovers Cruzeiro and Mirassol names when prediction payload only has ids", async ({ page }) => {
  await page.route("**/api/upcoming?*", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok",
      count: 1,
      fixtures: [{
        match_id: 401991001,
        league_slug: "bra.1",
        home_team_id: 2022,
        away_team_id: 9169,
        home_team_name: "Cruzeiro",
        away_team_name: "Mirassol",
        kickoff_ts: "2030-08-12T22:00:00Z",
      }],
    }),
  }));
  await page.route("**/api/predict/upcoming", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      match_id: 401991001,
      home_team_id: 2022,
      away_team_id: 9169,
      probability_home: 0.47,
      probability_draw: 0.29,
      probability_away: 0.24,
      probability_over_2_5: 0.54,
      probability_btts: 0.49,
      expected_home_goals: 1.51,
      expected_away_goals: 0.94,
      lambda_home: 1.51,
      lambda_away: 0.94,
      model: "selective_dc_kalman_official",
      experimental_team_markets: {
        user_market_view: [
          { period: "full_match", team_side: "home", metric: "shots", line: 10.5, probability: 0.64 },
          { period: "full_match", team_side: "away", metric: "corners", line: 3.5, probability: 0.57 },
        ],
      },
    }),
  }));

  await page.goto("/predictions/401991001?league=bra.1&home=2022&away=9169&kickoff=2030-08-12T22%3A00%3A00Z");

  await expect(page.getByRole("heading", { name: "Cruzeiro vs Mirassol" })).toBeVisible();
  await expect(page.getByLabel("Gráfica comparativa de probabilidades")).toBeVisible();
  await expect(page.getByText("Comparativa matemática del partido")).toBeVisible();
  await expect(page.getByText("Cruzeiro · Tiros · más de 10.5")).toBeVisible();
  await expect(page.getByText("Mirassol · Córners · más de 3.5")).toBeVisible();
  await expect(page.getByText(/Local 2022|Visitante 9169/)).toHaveCount(0);
});

test("renders the real live score, source timestamp and next event", async ({ page }) => {
  const markets = { probability_home: 0.62, probability_draw: 0.24, probability_away: 0.14, probability_over_2_5: 0.51, probability_btts: 0.44 };
  let liveRequests = 0;
  await page.unroute("**/api/provider/predictor**");
  await page.route("**/api/provider/predictor**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "available",
      probabilities: { home: .68, draw: .2, away: .12 },
      history: [
        { minute: 1, home: .45, draw: .3, away: .25 },
        { minute: 31, home: .68, draw: .2, away: .12 },
      ],
      market_context: { status: "financial_isolated_available" },
    }),
  }));
  await page.route("**/api/predict/live", (route) => {
    liveRequests += 1;
    return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      fixture: {
        home_team_name: "Real Madrid", away_team_name: "Barcelona",
        home_team_logo: "https://example.com/real.png", away_team_logo: "https://example.com/barca.png",
        score_home: 2, score_away: 1, match_clock_seconds: 1920,
        provider_status_detail: "32'", source_fetched_at: "2026-08-08T20:32:00Z",
      },
      observed_live_statistics: {
        source: "provider_play_by_play",
        home: { goals: 2, shots: 8, shots_on_target: 5, shots_off_target: 2, shots_blocked: 1, corners: 4, yellow_cards: 1, red_cards: 0, fouls: 7, offsides: 1, saves: 2, substitutions: 0 },
        away: { goals: 1, shots: 6, shots_on_target: 3, shots_off_target: 2, shots_blocked: 1, corners: 3, yellow_cards: 2, red_cards: 0, fouls: 9, offsides: 2, saves: 3, substitutions: 0 },
      },
      recent_actions: [{ event_id: "a1", event_type: "goal", event_type_raw: "goal", team_side: "home", team_name: "Real Madrid", minute: 31, text: "Gol de Real Madrid" }],
      match_dynamics: {
        current_minute: 33,
        points: Array.from({ length: 33 }, (_, index) => ({
          minute: index + 1,
          raw_score: index === 30 ? 25 : 0,
          smoothed_score: index >= 28 && index <= 32 ? 5 : 0,
          home_pressure: index >= 28 && index <= 32 ? 5 : 0,
          away_pressure: 0,
        })),
        goal_markers: [{ minute: 31, team_side: "home", team_name: "Real Madrid" }],
      },
      official_source: "live_probability_engine_v1",
      official_live_prediction: {
        status: "official", markets,
        remaining_intensities: { home: 1.1, away: 0.5 },
        next_event: { horizon_minutes: 5, probabilities: { "home:goal": 0.12, "away:goal": 0.06 }, probability_no_event: 0.82 },
        exact_score: [{ score_home: 2, score_away: 1, probability: 0.22 }, { score_home: 3, score_away: 1, probability: 0.16 }],
        periods: {
          first_half: { markets }, second_half: { markets }, full_time: { markets },
        },
        goal_horizons: {
          next_5m: { probability_any_goal: 0.14 }, next_10m: { probability_any_goal: 0.25 },
        },
        confidence: { level: "medium" },
        fallback: { applied: false },
      },
      live_probability_engine: {
        dynamic_poisson: { integration: "piecewise_5_minute", remaining_seconds: 3480 },
        ctmc: { dominant: "home_pressure", model: "ctmc_regime_v1" },
        hazard: { model: "bounded_cox_hazard_v1", features_are_live_only: true },
        dynamic_elo: { prior_difference: 40, live_difference: 62, shrinkage: 0.23 },
        hawkes_residual: { status: "official_component_residual", model_version: "hawkes_live_v2", rho: 1 },
        monte_carlo_diagnostic: { status: "scheduled", simulations: 20000 },
        audit: { passed: true },
      },
      experimental_markov_live: {
        status: "experimental_shadow_not_promoted", markets,
        next_event: { horizon_minutes: 5, probabilities: { "home:goal": 0.12 }, probability_no_event: 0.82 },
      },
      experimental_hawkes_residual: { status: "experimental_shadow_not_promoted" },
      experimental_combined_live: { status: "experimental_shadow_not_promoted", markets },
      hawkes_league_admission: { admitted: true },
    }),
    });
  });
  await page.goto("/live/900001?league=esp.1");
  await expect(page.getByAltText("Real Madrid")).toBeVisible();
  await expect(page.getByAltText("Barcelona")).toBeVisible();
  await expect(page.locator(".live-score span").nth(0)).toHaveText("2");
  await expect(page.locator(".live-score span").nth(1)).toHaveText("1");
  await expect(page.getByRole("heading", { name: "Acciones observadas" })).toBeVisible();
  await expect(page.getByText("Córners")).toBeVisible();
  await expect(page.getByText("Gol de Real Madrid")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Presión por acciones" })).toBeVisible();
  await expect(page.getByLabel("Curva de presión de Real Madrid y Barcelona")).toBeVisible();
  await expect(page.getByText("PREDICCIONES EN TIEMPO REAL")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Motor probabilístico in-live v1" })).toBeVisible();
  await expect(page.getByText("Auditoría matemática aprobada")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Predictor del proveedor" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Expectativa de resultado" })).toBeVisible();
  await expect(page.getByText(/PRÓXIMO EVENTO/)).toBeVisible();
  await expect(page.getByText("32:00", { exact: true })).toBeVisible();
  await expect(page.getByText(/Sincronizado/)).toBeVisible();
  await expect(page.getByText(/Actualización automática cada 15 s/)).toBeVisible();
  await expect.poll(() => liveRequests, { timeout: 18_000 }).toBeGreaterThanOrEqual(2);
});

const LIVE_MARKETS = { probability_home: 0.62, probability_draw: 0.24, probability_away: 0.14 };

function livePayload(teamMarkets: unknown) {
  return {
    fixture: {
      home_team_name: "Real Madrid", away_team_name: "Barcelona",
      score_home: 1, score_away: 0, match_clock_seconds: 1800,
      provider_status_detail: "30'", source_fetched_at: "2026-08-08T20:30:00Z",
    },
    official_source: "live_probability_engine_v1",
    official_live_prediction: {
      status: "official", markets: LIVE_MARKETS,
      remaining_intensities: { home: 1.1, away: 0.5 },
      next_event: { horizon_minutes: 5, probabilities: { "home:goal": 0.12 }, probability_no_event: 0.82 },
      exact_score: [], periods: { first_half: { markets: LIVE_MARKETS }, second_half: { markets: LIVE_MARKETS }, full_time: { markets: LIVE_MARKETS } },
      goal_horizons: { next_5m: { probability_any_goal: 0.14 }, next_10m: { probability_any_goal: 0.25 } },
      confidence: { level: "medium" }, fallback: { applied: false },
    },
    live_probability_engine: { audit: { passed: true }, monte_carlo_diagnostic: { status: "scheduled", simulations: 20000 } },
    ...(teamMarkets === undefined ? {} : { experimental_live_team_markets: teamMarkets }),
  };
}

test("renders adaptive remaining corners, shots and next goal in live detail", async ({ page }) => {
  const ladder = (line: number, over: number, base: number) => ({
    line, over_probability: over, under_probability: 1 - over,
    baseline_over_probability: base, baseline_under_probability: 1 - base,
  });
  await page.route("**/api/predict/live", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(livePayload({
      status: "experimental_shadow_not_promoted",
      remaining_seconds: 3600,
      next_goal: {
        remaining_minutes: 60, probability_home_next_goal: 0.5,
        probability_away_next_goal: 0.35, probability_no_more_goals: 0.15,
      },
      bounded_market_grid_view: [
        {
          key: "home_corners_remaining", metric: "corners", team_side: "home",
          expected_remaining: 3.73,
          lines: [ladder(2.5, 0.72, 0.66), ladder(3.5, 0.51, 0.47), ladder(4.5, 0.32, 0.29)],
        },
        {
          key: "away_corners_remaining", metric: "corners", team_side: "away",
          expected_remaining: 3.11,
          lines: [ladder(1.5, 0.82, 0.78), ladder(2.5, 0.6, 0.56), ladder(3.5, 0.38, 0.34)],
        },
        {
          key: "home_shots_remaining", metric: "shots", team_side: "home",
          expected_remaining: 9.89,
          lines: [ladder(7.5, 0.77, 0.7), ladder(8.5, 0.66, 0.58), ladder(9.5, 0.53, 0.46)],
        },
      ],
    })),
  }));
  await page.goto("/live/900001?league=esp.1");
  const block = page.locator("article.model-card").filter({ hasText: "Córners, tiros y próximo gol" });
  await expect(block).toBeVisible();
  await expect(block.getByText("PRÓXIMO GOL · 60.0 MIN RESTANTES")).toBeVisible();
  await expect(block.getByText("Sin más goles")).toBeVisible();
  await expect(block.getByText("15%", { exact: true })).toBeVisible();
  await expect(block.getByText("Real Madrid · Córners restantes")).toBeVisible();
  await expect(block.getByText("Barcelona · Córners restantes")).toBeVisible();
  await expect(block.getByText("Real Madrid · Tiros restantes")).toBeVisible();
  await expect(block.getByText("Más de 4.5", { exact: false })).toBeVisible();
  await expect(block.getByText("Más de 1.5", { exact: false })).toBeVisible();
  await expect(block.getByText("vs ritmo base +6.0 pp", { exact: false })).toBeVisible();
});

test("hides the remaining market block when the live engine falls back", async ({ page }) => {
  await page.route("**/api/predict/live", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(livePayload({
      status: "unavailable_fallback_active", reason: "FloatingPointError",
      bounded_market_grid_view: [], next_goal: {},
    })),
  }));
  await page.goto("/live/900001?league=esp.1");
  await expect(page.getByRole("heading", { name: "Motor probabilístico in-live v1" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Córners, tiros y próximo gol" })).toHaveCount(0);
});

test("navigates the complete historical match flow", async ({ page }) => {
  await page.goto("/explore");
  await page.getByRole("link", { name: /Partidos históricos/ }).click();
  await page.getByLabel("Liga").selectOption("mex.1");
  await page.getByLabel("Fecha").selectOption("20260808");
  await page.getByRole("link", { name: /Cruz Azul/ }).click();
  await expect(page.getByText("Estadio Central")).toBeVisible();
  await expect(page.getByText("Gol de Cruz Azul")).toBeVisible();
  await expect(page.getByRole("table", { name: "Estadísticas total" })).toBeVisible();
  await page.getByRole("button", { name: "1T" }).click();
  await expect(page.getByRole("table", { name: "Estadísticas first_half" })).toBeVisible();
});

test("navigates league, team, roster and player profile", async ({ page }) => {
  await page.goto("/explore/teams");
  await page.getByLabel("Liga").selectOption("mex.1");
  await page.getByLabel("Buscar equipo").fill("Cruz");
  await page.getByRole("link", { name: /Cruz Azul/ }).click();
  await expect(page.getByRole("heading", { name: "Cruz Azul" })).toBeVisible();
  await page.getByRole("link", { name: /Jugador Azul/ }).click();
  await expect(page.getByRole("heading", { name: "Jugador Azul" })).toBeVisible();
  await expect(page.getByText("7", { exact: true })).toBeVisible();
});

test("shows BFF to DIKAMAHA connection status", async ({ page }) => {
  await page.goto("/status");
  await expect(page.getByRole("heading", { name: "Estado del sistema" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Conectada" })).toBeVisible();
  await expect(page.getByText(/navegador → BFF → API DIKAMAHA/)).toBeVisible();
});

function settlement(index: number, hit: boolean) {
  return {
    fixture_key: `esp.1:90000${index}`,
    league_slug: "esp.1",
    match_id: 900000 + index,
    kickoff_ts: `2026-08-0${index + 1}T20:00:00Z`,
    home_team_name: "Real Madrid",
    away_team_name: "Barcelona",
    score_home: hit ? 2 : 0,
    score_away: hit ? 0 : 2,
    prediction_hash: `abc123def${index}`,
    official_verdicts: {
      one_x_two: { predicted: "Real Madrid", actual: hit ? "Real Madrid" : "Barcelona", hit },
      over_2_5: { predicted: "No", actual: "No", hit: true },
      btts: { predicted: "No", actual: "No", hit: true },
    },
    shadow_verdicts: {},
  };
}

test("shows verified hits with confidence interval and baseline", async ({ page }) => {
  await page.route("**/api/track-record**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "available",
      window: { requested: 60, available: 24 },
      official: {
        one_x_two: {
          hits: 14, total: 24, rate: 0.5833, sufficient_sample: true,
          interval_95: [0.3866, 0.7541], baseline_rate: 0.5,
        },
        over_2_5: { hits: 15, total: 24, rate: 0.625, sufficient_sample: true, interval_95: [0.4239, 0.7943], baseline_rate: 0.5417 },
        btts: { hits: 13, total: 24, rate: 0.5417, sufficient_sample: true, interval_95: [0.3495, 0.7203], baseline_rate: 0.5 },
      },
      shadow: { status: "experimental_not_promoted", markets: { home_corners_remaining_over_4_5: { hits: 3, total: 5 } } },
      matches: [settlement(0, true), settlement(1, false)],
      disclosure: "Cada predicción se congeló y publicó antes del kickoff.",
    }),
  }));
  await page.goto("/historial");
  await expect(page.getByRole("heading", { name: "Historial de aciertos" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mercados oficiales" })).toBeVisible();
  await expect(page.getByText("14/24")).toBeVisible();
  await expect(page.getByText(/Entre 39% y 75% · referencia 50%/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mercados experimentales" })).toBeVisible();
  await expect(page.getByText(/Sin validación confirmatoria/)).toBeVisible();
  await expect(page.getByText("✅").first()).toBeVisible();
  await expect(page.getByText("❌").first()).toBeVisible();
  await expect(page.getByText(/congeló y publicó antes del kickoff/)).toBeVisible();
});

test("withholds the percentage until the sample is large enough", async ({ page }) => {
  await page.route("**/api/track-record**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "available",
      window: { requested: 60, available: 6 },
      official: {
        one_x_two: { hits: 4, total: 6, sufficient_sample: false, missing_for_rate: 14 },
        over_2_5: { hits: 3, total: 6, sufficient_sample: false, missing_for_rate: 14 },
        btts: { hits: 2, total: 6, sufficient_sample: false, missing_for_rate: 14 },
      },
      shadow: { status: "experimental_not_promoted", markets: {} },
      matches: [settlement(0, true)],
      disclosure: "Cada predicción se congeló y publicó antes del kickoff.",
    }),
  }));
  await page.goto("/historial");
  await expect(page.getByText(/Faltan 14 partidos verificados/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Mercados experimentales" })).toHaveCount(0);
});
