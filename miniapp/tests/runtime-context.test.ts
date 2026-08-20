import { afterEach, describe, expect, it, vi } from "vitest";

import { detectContext } from "@/lib/runtime-context";

/**
 * Detección del contexto de ejecución (Fase 133).
 *
 * La señal es `initData`, no la presencia de `window.Telegram`: el SDK de
 * Telegram se carga en las dos superficies -está en `app/layout.tsx` sin
 * condición- así que el objeto existe también en un navegador normal. Confundir
 * las dos cosas haría que el sitio web intentara autenticarse como si fuera el
 * WebView y fallara en cada visita.
 */

afterEach(() => { vi.unstubAllGlobals(); });

function stubTelegram(webApp: unknown) {
  vi.stubGlobal("window", { Telegram: webApp ? { WebApp: webApp } : undefined });
}

describe("contexto de ejecución", () => {
  it("es telegram cuando hay datos firmados del WebView", () => {
    stubTelegram({ initData: "auth_date=1&hash=abc" });
    expect(detectContext()).toBe("telegram");
  });

  it("es web cuando el SDK está cargado pero sin initData", () => {
    // El caso real de un navegador: `telegram-web-app.js` define el objeto y
    // deja `initData` vacío.
    stubTelegram({ initData: "" });
    expect(detectContext()).toBe("web");
  });

  it("es web cuando no hay SDK en absoluto", () => {
    stubTelegram(undefined);
    expect(detectContext()).toBe("web");
  });
});
