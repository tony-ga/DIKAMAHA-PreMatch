import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  use: {
    baseURL: "http://127.0.0.1:3100",
    channel: "chrome",
    viewport: { width: 390, height: 844 },
  },
  /**
   * Dos superficies, un solo código (Fase 133).
   *
   * `miniapp` conserva exactamente lo que había -390x844 y el stub de
   * `window.Telegram` que cada spec inyecta-, y es la prueba de que la Mini App
   * no cambió. `web` corre **sin** ese stub y en tamaño de escritorio: es el
   * único sitio donde se comprueba que la aplicación funciona cuando no hay
   * WebView que la contenga.
   */
  projects: [
    { name: "miniapp", testIgnore: "**/web/**" },
    {
      name: "web",
      testMatch: "**/web/*.spec.ts",
      use: { viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
