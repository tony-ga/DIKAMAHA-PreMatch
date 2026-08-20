/**
 * Dónde se está ejecutando la aplicación.
 *
 * El mismo despliegue sirve la Mini App dentro de Telegram y el sitio web
 * público: no hay variable de entorno que lo distinga, porque una variable
 * obligaría a dos despliegues del mismo código y con ello a que divergieran.
 *
 * La señal es `initData`. No la mera presencia de `window.Telegram` -el SDK se
 * carga siempre, así que el objeto existe también en un navegador normal- sino
 * que traiga datos firmados, que es justo lo que la ruta de sesión necesita.
 * Es la misma comprobación que hasta ahora producía el error "Abre DIKAMAHA
 * desde Telegram", de modo que el criterio ya está probado en producción.
 */
export type RuntimeContext = "telegram" | "web";

export function detectContext(): RuntimeContext {
  if (typeof window === "undefined") return "web";
  return window.Telegram?.WebApp?.initData ? "telegram" : "web";
}
