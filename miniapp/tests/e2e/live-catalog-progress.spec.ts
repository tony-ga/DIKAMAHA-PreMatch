import { expect, test } from "@playwright/test";

/**
 * Un barrido en frío mide ~30s reales contra ESPN (63 ligas x 3 días,
 * DEC-181): antes la Mini App sólo mostraba "Escaneando ligas" fijo durante
 * toda esa espera, sin ninguna señal de avance. Estas pruebas cubren que la
 * barra ahora refleja el progreso real que reporta el servidor
 * (`GET /v1/live/progress`), no una animación indeterminada inventada.
 */

async function stub(page: import("@playwright/test").Page) {
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
  await page.route("**/api/explorer/leagues", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ leagues: [{ slug: "esp.1", name: "LaLiga" }], count: 1 }),
  }));
}

test("shows a real progress bar tied to leagues actually scanned", async ({ page }) => {
  await stub(page);

  // El patrón "**/api/live**" también encaja con "/api/live/progress", y
  // Playwright prueba primero el handler registrado más recientemente:
  // el catálogo (patrón amplio) debe registrarse ANTES que el progreso
  // (patrón específico) para que este último no quede tapado.
  await page.route("**/api/live**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 2_500));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", count: 0, fixtures: [], league_count: 63, date_count: 3 }),
    });
  });
  let progressCalls = 0;
  await page.route("**/api/live/progress**", (route) => {
    progressCalls += 1;
    // Avanza de verdad en cada sondeo sucesivo, como haría el servidor real.
    const scanned = Math.min(progressCalls * 40, 150);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "scanning", scanned, total: 189 }),
    });
  });

  await page.goto("/live");

  await expect(page.getByRole("heading", { name: "Escaneando ligas" })).toBeVisible();
  // El texto declara la fracción real, no un mensaje genérico de espera.
  await expect(page.getByText(/de 189 combinaciones liga\/fecha revisadas/)).toBeVisible({ timeout: 3_000 });

  const fill = page.locator(".progress-fill-real");
  await expect(fill).toBeVisible();
  const firstWidth = await fill.evaluate((el) => (el as HTMLElement).style.width);

  // El ancho crece con sondeos sucesivos -no es una animación de vaivén fija-.
  await expect(async () => {
    const width = await fill.evaluate((el) => (el as HTMLElement).style.width);
    expect(width).not.toBe(firstWidth);
  }).toPass({ timeout: 2_000 });

  // Al resolver el catálogo, el panel de progreso desaparece.
  await expect(page.getByRole("heading", { name: "No hay partidos activos" })).toBeVisible({ timeout: 3_000 });
  await expect(page.locator(".progress-track")).toHaveCount(0);
});

test("falls back to an honest generic message before any progress is known", async ({ page }) => {
  await stub(page);

  // Registro del catálogo primero: ver nota de la prueba anterior sobre por
  // qué el patrón de progreso debe registrarse después para no quedar tapado.
  await page.route("**/api/live**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ status: "ok", count: 0, fixtures: [] }),
    });
  });
  await page.route("**/api/live/progress**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "idle", scanned: 0, total: 0 }),
  }));

  await page.goto("/live");

  // Sin total conocido todavía (`idle`), no se inventa un porcentaje: se usa
  // el mismo mensaje honesto que ya usa `LoadingProgress` en otras pantallas.
  await expect(page.getByText("Consultando fixtures activos en DIKAMAHA.")).toBeVisible();
  await expect(page.locator(".progress-fill-real")).toHaveCount(0);
  await expect(page.locator(".progress-fill")).toBeVisible();
});
