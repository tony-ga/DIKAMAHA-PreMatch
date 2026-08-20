import type { MetadataRoute } from "next";

import { publicWebUrl } from "@/lib/env";

/**
 * Qué puede indexar un buscador.
 *
 * Casi nada, y a propósito. El producto vive detrás de una sesión: rastrear
 * `/predictions` o `/live` sólo produciría páginas de error en los resultados.
 * Lo que sí tiene sentido público son la portada, la pantalla de acceso y las
 * tarjetas compartidas, que existen precisamente para abrirse desde fuera.
 */
// Por la misma razón que `/login`: lee el dominio de la configuración, que sólo
// existe en tiempo de ejecución.
export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  const origin = publicWebUrl();
  return {
    rules: [{
      userAgent: "*",
      allow: ["/", "/login", "/s/"],
      disallow: [
        "/api/", "/admin", "/settings", "/live", "/predictions", "/explore",
        "/historial", "/upcoming", "/markets", "/models", "/constructor",
        "/mayor-probabilidad", "/subscriptions", "/status",
      ],
    }],
    ...(origin ? { host: origin, sitemap: `${origin}/sitemap.xml` } : {}),
  };
}
