import type { Metadata, Viewport } from "next";
import { Space_Grotesk } from "next/font/google";
import Script from "next/script";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { Providers } from "@/components/providers";
import "./globals.css";

const font = Space_Grotesk({ subsets: ["latin"], display: "swap" });

export const metadata: Metadata = {
  title: "DIKAMAHA | Live Intelligence",
  description: "Predicciones pre-match y live con Markov y Hawkes dentro de Telegram.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // Se quitó una vez por accesibilidad (impedía el pellizco manual) y hubo
  // que restaurarlo: un reporte real desde un iPhone 12 Pro mostró la app
  // abriendo ya zoomeada, con el borde derecho cortado, hasta que el usuario
  // alejaba el zoom con dos dedos — el patrón exacto del WebView de Telegram
  // calculando mal su escala inicial cuando `maximum-scale` está ausente. Sin
  // este valor, WebKit puede "ajustar a lo que ve" en el primer pintado (antes
  // de que React termine de hidratar) y quedarse en ese zoom aunque el layout
  // real quepa perfecto a escala 1. Este es un WebView embebido en Telegram,
  // no un sitio abierto: el usuario ya cuenta con el zoom del sistema (Zoom /
  // Texto más grande de iOS) si necesita ampliar algo, así que el costo de
  // accesibilidad es mucho menor que el beneficio de que la app abra completa
  // sin ajuste manual.
  maximumScale: 1,
  viewportFit: "cover",
  themeColor: "#091413",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="es" suppressHydrationWarning>
      <body className={font.className}>
        <Script src="https://telegram.org/js/telegram-web-app.js?63" strategy="beforeInteractive" />
        <Providers><AppShell>{children}</AppShell></Providers>
      </body>
    </html>
  );
}
