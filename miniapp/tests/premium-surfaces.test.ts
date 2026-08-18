import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Qué superficies están detrás del muro de pago.
 *
 * No hay forma de comprobarlo desde el servidor: `/live` y
 * `/mayor-probabilidad` se gatean en su ruta de API -que devuelve 402 y no
 * llega a llamar a DIKAMAHA-, pero el constructor de picks calcula en el
 * navegador desde predicciones que el usuario ya descargó, así que su gateo
 * vive por entero en el cliente y sólo se puede verificar de forma estática.
 *
 * Estas comprobaciones existen porque un gateo de cliente se pierde en
 * silencio: basta que alguien reordene un `return` para que la pantalla vuelva
 * a ser gratuita sin que falle ningún test de comportamiento.
 */

function source(...segments: string[]): string {
  return readFileSync(resolve(process.cwd(), ...segments), "utf8");
}

describe("superficies de pago en la Mini App", () => {
  it("la barra inferior marca las tres pantallas de pago", () => {
    const shell = source("components", "app-shell.tsx");
    for (const href of ["/live", "/mayor-probabilidad", "/constructor"]) {
      const entry = shell
        .split("\n")
        .find((line) => line.includes(`href: "${href}"`));
      expect(entry, `no hay entrada de navegación para ${href}`).toBeDefined();
      expect(entry, `${href} no está marcado como premium`).toMatch(/premium:\s*true/);
    }
  });

  it("la pantalla del constructor consulta el plan y ofrece el muro", () => {
    const page = source("app", "constructor", "page.tsx");
    expect(page).toMatch(/usePremium\(\)/);
    expect(page).toMatch(/PremiumUpsell/);
    // El muro tiene que estar antes del cálculo visible, no después.
    expect(page.indexOf("if (!premium)")).toBeGreaterThan(-1);
    expect(page.indexOf("if (!premium)")).toBeLessThan(page.indexOf("joint.matches.map"));
  });

  it("los botones que alimentan el constructor no se pintan sin plan", () => {
    // Cubre las tres pantallas que insertan `PickToggle` -detalle de
    // predicción, escalera auditada y rejilla de mercados- de una sola vez:
    // si el botón se oculta en su propio componente, ninguna de ellas puede
    // ofrecerlo por su cuenta.
    const toggle = source("components", "pick-toggle.tsx");
    expect(toggle).toMatch(/usePremium\(\)/);
    expect(toggle).toMatch(/if \(!premium\) return null;/);
    expect(toggle).toMatch(/if \(!premium \|\| !picks\.length\) return null;/);
  });

  it("el constructor aparece en lo que se anuncia como Premium", () => {
    // Gatear algo que no se anuncia deja al usuario sin saber qué compraría.
    expect(source("components", "premium-gate.tsx")).toMatch(/Constructor de picks/i);
  });
});
