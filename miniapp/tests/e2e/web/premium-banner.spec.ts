import { expect, test, type Page } from "@playwright/test";

/**
 * Superficies comerciales fuera del muro.
 *
 * Lo que se protege aquí no es el aspecto sino **a quién se le pide dinero**:
 * un banner de compra delante de alguien que ya paga, o mientras el cobro está
 * apagado, es el peor fallo posible de esta capa y no lo detecta ninguna prueba
 * visual. Las reglas puras están en `tests/premium-banner.test.ts`; esto
 * comprueba que la interfaz las respeta de verdad al montarlas.
 */

type Plan = { plan: "free" | "premium"; enforced: boolean; remaining: number };

async function boot(page: Page, entitlement: Plan) {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200, contentType: "application/javascript", body: "",
  }));
  await page.route("**/api/**", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ status: "ok", count: 3, fixtures: [], favorites: [], models: [] }),
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ user: { id: 42, firstName: "Marco" }, csrfToken: "t" }),
  }));
  await page.route("**/api/billing/entitlement", (route) => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      plan: entitlement.plan,
      planSource: entitlement.plan === "premium" ? "stripe" : "default",
      role: "user",
      expiresAt: null,
      enforced: entitlement.enforced,
      starsAmount: 250,
      webCheckout: true,
      quota: entitlement.plan === "free"
        ? { used: 3 - entitlement.remaining, limit: 3, remaining: entitlement.remaining }
        : null,
    }),
  }));
}

test("con la cuota agotada lo dice y ofrece quitar el límite", async ({ page }) => {
  await boot(page, { plan: "free", enforced: true, remaining: 0 });
  await page.goto("/");

  await expect(page.getByText("Has usado tus 3 predicciones de hoy")).toBeVisible();
  // La frase que juega en contra de la venta y por eso importa: nadie debe
  // pagar creyendo que perdió el acceso para siempre.
  await expect(page.getByText(/Se renuevan solas cada día/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Quitar el límite/ })).toBeVisible();
});

test("con el día por delante no interrumpe", async ({ page }) => {
  await boot(page, { plan: "free", enforced: true, remaining: 3 });
  await page.goto("/");

  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();
  await expect(page.getByText(/predicciones de hoy/)).toHaveCount(0);
  // El destacado sí puede estar: no depende de que la cuota apriete.
  await expect(page.getByText("DIKAMAHA PREMIUM")).toBeVisible();
});

test("a quien ya paga no se le vende nada", async ({ page }) => {
  await boot(page, { plan: "premium", enforced: true, remaining: 0 });
  await page.goto("/");

  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();
  await expect(page.getByText("DIKAMAHA PREMIUM")).toHaveCount(0);
  await expect(page.getByText(/predicciones de hoy/)).toHaveCount(0);
});

test("con el cobro apagado no se ofrece Premium en ninguna parte", async ({ page }) => {
  // Nada está gateado, así que pedir dinero sería cobrar por lo que se regala.
  await boot(page, { plan: "free", enforced: false, remaining: 0 });
  await page.goto("/");

  await expect(page.getByRole("navigation", { name: "Navegación principal" })).toBeVisible();
  await expect(page.getByText("DIKAMAHA PREMIUM")).toHaveCount(0);
  await expect(page.getByText(/predicciones de hoy/)).toHaveCount(0);
});
