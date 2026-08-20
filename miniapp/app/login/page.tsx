import type { Metadata } from "next";

import { LoginWidget } from "@/components/login-widget";
import { env } from "@/lib/env";

/**
 * Se renderiza por petición, no al construir la imagen.
 *
 * Esta pantalla lee el nombre del bot de la configuración, y la configuración
 * la pone Railway como variables **del servicio**: durante `npm run build`
 * dentro del Dockerfile no existe ninguna. Sin esto, prerenderizar `/login`
 * llamaba a `env()` con el entorno vacío y **rompía la compilación de la
 * imagen** -no en producción, sino antes de llegar a ella-.
 */
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Entrar | DIKAMAHA",
  description: "Accede a DIKAMAHA con tu cuenta de Telegram.",
};

/**
 * Puerta de entrada del sitio web.
 *
 * Es un componente de servidor sólo para leer el nombre del bot de la
 * configuración y pasárselo al widget: exponerlo como `NEXT_PUBLIC_*` lo
 * congelaría en el momento de construir la imagen, y este servicio se
 * configura por variables de entorno del servicio, no del build.
 */
export default function LoginPage() {
  return (
    <main className="launch-screen login-screen">
      <div className="brand-mark">DK</div>
      <p className="eyebrow">DIKAMAHA LIVE INTELLIGENCE</p>
      <h1>Entra con Telegram</h1>
      <p className="muted">
        Tu cuenta es la misma dentro y fuera de Telegram: mismas predicciones,
        mismo historial y mismo plan.
      </p>
      <LoginWidget botUsername={env().TELEGRAM_BOT_USERNAME} />
    </main>
  );
}
