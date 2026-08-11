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
  // Sin `maximumScale`: fijarlo en 1 impedía ampliar con los dedos, que es un
  // requisito de accesibilidad y además el recurso natural del usuario cuando
  // una tabla no cabe. iOS lo ignora desde iOS 10, así que sólo perjudicaba.
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
