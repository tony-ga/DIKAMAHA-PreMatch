import { expect, test, type Page } from "@playwright/test";

/**
 * La aplicación fuera de Telegram (Fase 133).
 *
 * A diferencia del resto de la suite, aquí **no** se inyecta el stub de
 * `window.Telegram`: se sirve el SDK vacío, que es lo que ocurre de verdad en
 * un navegador -el script se carga igual, pero no hay WebView que lo conteste,
 * así que `initData` nunca llega-. Esa ausencia es exactamente la señal con la
 * que `lib/runtime-context.ts` decide el contexto, y todo lo que se prueba aquí
 * depende de que la decida bien.
 */

async function browserWithoutTelegram(page: Page) {
  // Sin salir a telegram.org: la suite no debe depender de la red, y el efecto
  // que interesa -`window.Telegram` indefinido- es justo el de un script vacío.
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200, contentType: "application/javascript", body: "",
  }));
}

async function withSession(page: Page) {
  await page.route("**/api/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ status: "ok", count: 0, fixtures: [], favorites: [] }),
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "t" }),
  }));
}

test("sin sesión lleva a la pantalla de acceso en vez de pedir Telegram", async ({ page }) => {
  await browserWithoutTelegram(page);
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 401, contentType: "application/json",
    body: JSON.stringify({ error: "authentication_required" }),
  }));

  await page.goto("/");

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Entra con Telegram" })).toBeVisible();
  // El mensaje del WebView no puede aparecer en un navegador: allí no hay nada
  // que "abrir desde Telegram" que resuelva el problema.
  await expect(page.getByText("Abre DIKAMAHA desde Telegram.")).toHaveCount(0);
});

test("con sesión sirve la aplicación completa, no una versión recortada", async ({ page }) => {
  await browserWithoutTelegram(page);
  await withSession(page);

  await page.goto("/");

  const nav = page.getByRole("navigation", { name: "Navegación principal" });
  // Mismas funciones: todos los destinos de la Mini App, ninguno menos. La
  // cuenta se afirma a propósito -si la web sirviera una versión recortada,
  // este número sería el primero en delatarlo-, así que añadir un destino
  // obliga a actualizarla aquí y en la prueba de anchura de la barra.
  await expect(nav.locator("a")).toHaveCount(8);
  await expect(page.getByRole("link", { name: "Ir al inicio" })).toBeVisible();
});

test("el marco se centra en escritorio en vez de estirarse", async ({ page }) => {
  await browserWithoutTelegram(page);
  await withSession(page);

  await page.goto("/");
  // Antes de medir: el marco sólo aparece cuando la sesión se resolvió, y es el
  // mismo efecto el que ajusta las barras. Medir antes lee el HTML servido, no
  // la página hidratada -y en paralelo con el resto de la suite eso se nota-.
  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();

  const layout = await page.evaluate(() => {
    const bar = document.querySelector(".topbar") as HTMLElement;
    const nav = document.querySelector(".bottom-nav") as HTMLElement;
    return {
      topbar: Math.round(bar.getBoundingClientRect().width),
      nav: Math.round(nav.getBoundingClientRect().width),
      viewport: document.documentElement.clientWidth,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });

  // Las dos barras están fijas al viewport: sin acotarlas, los siete destinos
  // quedarían separados a lo ancho de la pantalla.
  expect(layout.topbar).toBeLessThanOrEqual(820);
  expect(layout.nav).toBeLessThanOrEqual(820);
  expect(layout.topbar).toBeLessThan(layout.viewport);
  expect(layout.overflow).toBe(false);
});

test("libera el bloqueo de zoom que sólo existe por el WebView de iOS", async ({ page }) => {
  await browserWithoutTelegram(page);
  await withSession(page);

  await page.goto("/");
  // El bloqueo se retira en el mismo efecto que resuelve la sesión, así que
  // esperar al marco es esperar a que ya haya corrido.
  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();

  const viewport = await page.locator('meta[name="viewport"]').getAttribute("content");
  expect(viewport).not.toContain("maximum-scale");
});

test("no deja errores de consola por la ausencia de Telegram", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await browserWithoutTelegram(page);
  await withSession(page);

  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();

  expect(errors).toEqual([]);
});
