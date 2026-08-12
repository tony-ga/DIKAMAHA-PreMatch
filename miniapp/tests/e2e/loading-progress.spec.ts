import { expect, test, type Page } from "@playwright/test";

/**
 * Reporte real: abrir una predicción "tardaba muchísimo" sin ninguna señal de
 * que la app siguiera trabajando. La barra es deliberadamente indeterminada
 * (un segmento que se desliza, no un porcentaje) porque el servidor no
 * expone progreso real de una inferencia causal; lo honesto es reconocer el
 * tiempo transcurrido, no inventar un número que sube solo.
 */

async function stub(page: Page) {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/javascript",
    body: `window.Telegram ||= { WebApp: { initData:'', colorScheme:'dark', ready(){}, expand(){}, close(){}, onEvent(){}, offEvent(){}, BackButton:{show(){},hide(){},onClick(){},offClick(){}} } };`,
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "t" }),
  }));
  await page.route("**/api/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", count: 0, fixtures: [], favorites: [], picks: [] }),
  }));
}

test("shows a sliding progress bar while a slow prediction resolves", async ({ page }) => {
  await stub(page);
  await page.route("**/api/predict/upcoming", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 6_000));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        fixture: { home_team_name: "F.C. Copenhagen", away_team_name: "Debrecen" },
        probability_home: 0.55, probability_draw: 0.25, probability_away: 0.2,
        probability_over_2_5: 0.5, probability_btts: 0.45,
        expected_home_goals: 1.5, expected_away_goals: 1.0,
        lambda_home: 1.5, lambda_away: 1.0,
      }),
    });
  });

  await page.goto(
    "/predictions/401903200?league=uefa.europa.conf_qual&home=1&away=2" +
    "&homeName=F.C.%20Copenhagen&awayName=Debrecen&kickoff=2026-08-13T18%3A00%3A00Z");

  // A los 0-3s: el mensaje inicial, y la barra ya visible y animada.
  await expect(page.getByRole("heading", { name: "Calculando pre-match" })).toBeVisible();
  await expect(page.locator(".progress-track")).toBeVisible();
  await expect(page.locator(".progress-fill")).toBeVisible();
  await expect(page.getByText(/Resolviendo equipos/)).toBeVisible();

  // Pasados unos segundos, el mensaje cambia: sigue viva, no congelada.
  await expect(page.getByText(/Dixon-Coles y Kalman/)).toBeVisible({ timeout: 6_000 });

  // Y al resolver, desaparece y se muestra el resultado real.
  await expect(page.getByRole("heading", {
    name: "F.C. Copenhagen vs Debrecen",
  })).toBeVisible({ timeout: 4_000 });
  await expect(page.locator(".progress-track")).toHaveCount(0);
});

test("the animation actually moves, it is not a static bar", async ({ page }) => {
  await stub(page);
  await page.route("**/api/predict/upcoming", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 4_000));
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        fixture: { home_team_name: "A", away_team_name: "B" },
        probability_home: 0.4, probability_draw: 0.3, probability_away: 0.3,
        probability_over_2_5: 0.5, probability_btts: 0.5,
        expected_home_goals: 1.2, expected_away_goals: 1.1,
        lambda_home: 1.2, lambda_away: 1.1,
      }),
    });
  });

  await page.goto(
    "/predictions/401903201?league=esp.1&home=1&away=2" +
    "&homeName=A&awayName=B&kickoff=2026-08-13T18%3A00%3A00Z");

  const fill = page.locator(".progress-fill");
  await expect(fill).toBeVisible();
  const first = await fill.evaluate((el) => el.getBoundingClientRect().left);
  await page.waitForTimeout(700);
  const second = await fill.evaluate((el) => el.getBoundingClientRect().left);
  expect(second).not.toBeCloseTo(first, 0);
});
