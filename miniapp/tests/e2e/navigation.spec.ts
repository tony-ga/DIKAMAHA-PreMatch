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
  await expect(page.getByText("PREDICCIONES EN TIEMPO REAL")).toBeVisible();
  await expect(page.getByText(/PRÓXIMO EVENTO/)).toBeVisible();
  await expect(page.getByText("32:00", { exact: true })).toBeVisible();
  await expect(page.getByText(/Sincronizado/)).toBeVisible();
  await expect(page.getByText(/Actualización automática cada 10 s/)).toBeVisible();
  await expect.poll(() => liveRequests, { timeout: 12_000 }).toBeGreaterThanOrEqual(2);
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
