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

async function stub(page: Page, firstName = "Marco") {
  await page.route("https://telegram.org/js/**", (route) => route.fulfill({
    status: 200,
    contentType: "application/javascript",
    // `initData` no vacío desde la Fase 133: es la señal con la que
    // `lib/runtime-context.ts` distingue el WebView de un navegador, y este
    // archivo prueba justamente comportamiento del WebView -el bloqueo de
    // escala existe sólo ahí-. Un cliente de Telegram real siempre lo manda:
    // sin él la Mini App ni siquiera podría autenticarse.
    body: `window.Telegram ||= { WebApp: { initData:'auth_date=1&hash=stub', colorScheme:'dark', ready(){}, expand(){}, close(){}, onEvent(){}, offEvent(){}, BackButton:{show(){},hide(){},onClick(){},offClick(){}} } };`,
  }));
  await page.route("**/api/session/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user: { id: 42, firstName }, csrfToken: "t" }),
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

/**
 * Reporte real de un iPhone 12 Pro: la app abría zoomeada de más y había que
 * alejar el zoom con dos dedos para ver la pantalla completa; el nombre de
 * usuario en la cabecera ("• ga…") aparecía cortado en el borde derecho.
 *
 * Todas las pruebas anteriores usaban `firstName: "Marco"` (5 caracteres), que
 * nunca desborda, así que ninguna atrapó esto. `.user-chip` es un ítem flex
 * dentro de `.topbar` sin `min-width: 0`; por defecto un ítem flex se niega a
 * encoger por debajo del ancho de su contenido, así que un nombre real de
 * Telegram más largo empuja el chip más allá del ancho del dispositivo. Como
 * la barra está fija y presente desde el primer pintado (antes de que
 * cualquier dato cargue), ese desbordamiento es precisamente el tipo de causa
 * que hace que WebKit calcule un zoom inicial mayor que 1 en la carga.
 */
for (const device of DEVICES) {
  test(`header fits ${device.name} (${device.width}px) with a long Telegram name`, async ({ page }) => {
    await page.setViewportSize({ width: device.width, height: device.height });
    // Sin espacios: el caso que más exige, porque no hay punto de quiebre
    // natural para envolver la línea, sólo truncar.
    await stub(page, "Wolfeschlegelsteinhausenbergerdorff");

    await page.goto("/");
    await expect(page.getByRole("link", { name: "Abrir ajustes" })).toBeVisible();

    const result = await overflow(page);
    expect(result.offenders).toEqual([]);
    expect(result.documentScrollWidth).toBeLessThanOrEqual(device.width);
  });
}

test("truncates a long Telegram name instead of letting it overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await stub(page, "Wolfeschlegelsteinhausenbergerdorff");
  await page.goto("/");

  const chip = page.getByRole("link", { name: "Abrir ajustes" });
  const box = await chip.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  await expect(page.locator(".user-chip-name")).toHaveCSS("text-overflow", "ellipsis");
});

/**
 * `maximum-scale=1` es intencional en este WebView embebido en Telegram, no
 * un descuido de accesibilidad. Se probó SIN él y produjo un reporte real de
 * un iPhone 12 Pro: la app abría ya zoomeada de más, con el borde derecho
 * cortado, hasta pellizcar para alejar. Sin `maximum-scale`, WebKit puede
 * fijar la escala inicial según lo que ve en el primer pintado (antes de que
 * React termine de hidratar) y quedarse ahí aunque el layout real quepa
 * perfecto a escala 1. `user-scalable=no` sigue sin usarse: no bloquea el
 * pellizco del usuario, sólo evita que el WebView elija mal el punto de
 * partida.
 */
test("pins the initial scale without blocking manual zoom", async ({ page }) => {
  await stub(page);
  await page.goto("/");
  const content = await page.locator('meta[name="viewport"]').getAttribute("content");
  expect(content).toContain("width=device-width");
  expect(content).toContain("initial-scale=1");
  expect(content).toContain("maximum-scale=1");
  expect(content).not.toContain("user-scalable=no");
});
