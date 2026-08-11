import { expect, test, type Page } from "@playwright/test";

/**
 * La Mini App debe encuadrar completa en cualquier resolución.
 *
 * Existe por un reporte real desde un iPhone 12 Pro (390 x 844 px CSS): al
 * abrir una predicción había que alejar el zoom para ver toda la app, algo que
 * no ocurría en iPad ni escritorio porque allí el contenido sí cabía. La causa
 * medida fue `.prediction-table` con `white-space:nowrap` en todas las celdas:
 * con nombres de equipo largos fijaba un ancho mínimo de ~451 px.
 */

// Nombres deliberadamente largos: son el único contenido de longitud libre.
const PREDICTION = {
  fixture: {
    home_team_name: "Borussia Moenchengladbach",
    away_team_name: "Sporting Kansas City II",
  },
  probability_home: 0.4, probability_draw: 0.3, probability_away: 0.3,
  probability_over_2_5: 0.55, probability_btts: 0.52,
  expected_home_goals: 1.42, expected_away_goals: 1.08,
  lambda_home: 1.42, lambda_away: 1.08,
  experimental_team_markets: {
    user_market_view: [
      { period: "first_half", team_side: "home", metric: "shots", line: 5.5, probability: 0.61 },
      { period: "second_half", team_side: "away", metric: "shots", line: 5.5, probability: 0.58 },
      { period: "full_match", team_side: "home", metric: "corners", line: 4.5, probability: 0.63 },
    ],
  },
};

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
  await page.route("**/api/predict/upcoming", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(PREDICTION),
  }));
  await page.route("**/api/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      status: "ok", count: 0, fixtures: [], favorites: [], picks: [],
      provenance: { eligible_cells: 9 },
    }),
  }));
}

/** Devuelve los elementos cuyo borde derecho sale del viewport. */
async function overflow(page: Page) {
  return page.evaluate(() => {
    const viewport = document.documentElement.clientWidth;
    const offenders: string[] = [];
    document.querySelectorAll<HTMLElement>("*").forEach((element) => {
      const box = element.getBoundingClientRect();
      if (box.width === 0 && box.height === 0) return;
      if (box.right <= viewport + 0.5 && box.width <= viewport + 0.5) return;
      const parent = element.parentElement?.getBoundingClientRect();
      if (parent && (parent.right > viewport + 0.5 || parent.width > viewport + 0.5)) return;
      offenders.push(
        `${element.tagName.toLowerCase()}.${element.className}`.slice(0, 80));
    });
    return {
      offenders,
      documentScrollWidth: document.documentElement.scrollWidth,
      viewport,
    };
  });
}

const DEVICES = [
  { name: "iPhone SE", width: 375, height: 667 },
  { name: "iPhone 12 Pro", width: 390, height: 844 },
  { name: "Pixel 7", width: 412, height: 915 },
  { name: "iPad mini", width: 768, height: 1024 },
];

for (const device of DEVICES) {
  test(`prediction detail fits ${device.name} (${device.width}px)`, async ({ page }) => {
    await page.setViewportSize({ width: device.width, height: device.height });
    await stub(page);

    await page.goto(
      "/predictions/401880614?league=eng.league_cup&home=351&away=280" +
      "&homeName=Borussia%20Moenchengladbach&awayName=Sporting%20Kansas%20City%20II" +
      "&kickoff=2030-01-10T20%3A00%3A00Z");
    await expect(page.getByRole("heading", {
      name: "Borussia Moenchengladbach vs Sporting Kansas City II",
    })).toBeVisible();

    const result = await overflow(page);
    expect(result.offenders).toEqual([]);
    expect(result.documentScrollWidth).toBeLessThanOrEqual(device.width);
  });
}

test("the layout never blocks pinch zoom", async ({ page }) => {
  await stub(page);
  await page.goto("/");
  const content = await page.locator('meta[name="viewport"]').getAttribute("content");
  expect(content).toContain("width=device-width");
  expect(content).not.toContain("maximum-scale");
  expect(content).not.toContain("user-scalable=no");
});
