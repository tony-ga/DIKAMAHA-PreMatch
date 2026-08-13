import { expect, test } from "@playwright/test";

/**
 * Al reabrir la Mini App nada se pintaba hasta que `/api/session/me` respondía,
 * de modo que la primera petición de datos ni siquiera había salido cuando el
 * usuario ya llevaba un round-trip esperando. Con una sesión previa conocida la
 * interfaz se pinta de inmediato y las consultas salen en paralelo con la
 * confirmación de la sesión.
 *
 * La cookie sigue siendo lo único que autentica: si ya no vale, lo pintado se
 * descarta y se rehace el alta por Telegram.
 */

const emptyCatalog = { status: "ok", count: 0, fixtures: [] };
const LAST_SESSION_KEY = "dikamaha-miniapp-last-user";

function telegramStub() {
  window.Telegram = {
    WebApp: {
      initData: "query_id=test&auth_date=1786250000&hash=test",
      colorScheme: "dark",
      ready() {}, expand() {}, close() {},
      onEvent() {}, offEvent() {},
      BackButton: { show() {}, hide() {}, onClick() {}, offClick() {} },
    },
  };
}

test.beforeEach(async ({ page }) => {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200, contentType: "application/javascript", body: "",
  }));
  await page.route("**/api/live**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
  }));
  await page.route("**/api/upcoming**", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
  }));
  await page.route("**/api/models", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ status: "ok", models: [] }),
  }));
});

test("paints and requests data without waiting for the session check", async ({ page }) => {
  const order: string[] = [];
  await page.addInitScript(telegramStub);
  await page.addInitScript(([key, value]) => {
    window.localStorage.setItem(key, value);
  }, [LAST_SESSION_KEY, JSON.stringify({ id: 42, firstName: "Marco" })]);

  // `/api/session/me` tarda medio segundo: si las consultas de datos
  // esperasen a que resuelva, ninguna aparecería antes que ella.
  await page.route("**/api/session/me", async (route) => {
    order.push("session/me:start");
    await new Promise((resolve) => setTimeout(resolve, 500));
    order.push("session/me:end");
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "csrf-test" }),
    });
  });
  await page.route("**/api/upcoming**", (route) => {
    order.push("upcoming");
    return route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify(emptyCatalog),
    });
  });

  await page.goto("/");

  // El nombre recordado se pinta sin haber confirmado la sesión.
  await expect(page.getByRole("heading", { name: "Lee el partido. Antes y durante." })).toBeVisible();
  await expect(page.getByRole("link", { name: "Abrir ajustes" })).toContainText("Marco");
  // La prueba real: la petición de datos salió mientras `/api/session/me`
  // seguía en vuelo. Se espera a que ambas hayan terminado para comparar su
  // orden real, en vez de leer el registro a medio llenar.
  await expect.poll(() => order.includes("upcoming")).toBe(true);
  await expect.poll(() => order.includes("session/me:end")).toBe(true);
  expect(order.indexOf("upcoming")).toBeLessThan(order.indexOf("session/me:end"));
});

test("falls back to the Telegram sign-in when the remembered session is dead", async ({ page }) => {
  await page.addInitScript(telegramStub);
  await page.addInitScript(([key, value]) => {
    window.localStorage.setItem(key, value);
  }, [LAST_SESSION_KEY, JSON.stringify({ id: 42, firstName: "Marco" })]);

  let sessionChecks = 0;
  await page.route("**/api/session/me", (route) => {
    sessionChecks += 1;
    // La cookie ya no vale en la primera comprobación; tras rehacer el alta, sí.
    if (sessionChecks === 1) {
      return route.fulfill({
        status: 401, contentType: "application/json",
        body: JSON.stringify({ error: "authentication_required" }),
      });
    }
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ user: { id: 7, firstName: "Renovado" }, csrfToken: "csrf-test" }),
    });
  });
  await page.route("**/api/session/telegram", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ user: { id: 7, firstName: "Renovado" }, csrfToken: "csrf-test" }),
  }));

  await page.goto("/");

  // Lo optimista se descarta y gana la identidad que confirma el servidor.
  await expect(page.getByRole("link", { name: "Abrir ajustes" })).toContainText("Renovado");
  await expect(page.getByRole("heading", { name: "Lee el partido. Antes y durante." })).toBeVisible();
});

test("still blocks on the first ever open, with nothing remembered", async ({ page }) => {
  await page.addInitScript(telegramStub);
  let released: (() => void) | null = null;
  const gate = new Promise<void>((resolve) => { released = resolve; });
  await page.route("**/api/session/me", async (route) => {
    await gate;
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "csrf-test" }),
    });
  });

  await page.goto("/");

  // Sin sesión previa no hay nada que adelantar: las consultas sólo darían 401,
  // así que se conserva la pantalla de arranque.
  await expect(page.getByRole("heading", { name: "Preparando tu panel" })).toBeVisible();
  released!();
  await expect(page.getByRole("heading", { name: "Lee el partido. Antes y durante." })).toBeVisible();
});
