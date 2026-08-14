import type { Metadata } from "next";
import { headers } from "next/headers";
import { notFound } from "next/navigation";

import { shareCardByToken } from "@/lib/share-store";

type Props = { params: Promise<{ token: string }> };

/**
 * Página pública de una tarjeta compartida.
 *
 * No exige sesión: es lo que ve alguien que recibió el link por WhatsApp o
 * Telegram y no tiene cuenta. `AppShell` y el portero de Telegram se saltan
 * para esta ruta (ver `lib/public-routes.ts`), porque un visitante externo no
 * debe encontrarse la navegación de la aplicación ni una pantalla de acceso.
 *
 * El contenido es la imagen, no texto: se pidió explícitamente que lo que
 * circule sea una tarjeta con la marca de agua, no un mensaje reenviable y
 * editable. El `<img>` apunta al mismo PNG que la vista previa del link, así
 * que quien la reciba puede mantener pulsado y guardarla.
 */

async function absoluteBase(): Promise<string> {
  const list = await headers();
  const host = list.get("x-forwarded-host") ?? list.get("host") ?? "";
  const protocol = list.get("x-forwarded-proto") ?? "https";
  return `${protocol}://${host}`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { token } = await params;
  const card = await shareCardByToken(token);
  if (!card) return { title: "DIKAMAHA" };
  const title = `${card.homeName} vs ${card.awayName}`;
  const description =
    `Escenario principal: ${card.headlineLabel} `
    + `${Math.round(card.headlineProbability * 100)}%. `
    + "Predicción congelada antes del kickoff.";
  const image = `${await absoluteBase()}/s/${token}/image`;
  return {
    title: `${title} | DIKAMAHA`,
    description,
    openGraph: {
      title, description, type: "article",
      images: [{ url: image, width: 1080, height: 1350, alt: title }],
    },
    twitter: { card: "summary_large_image", title, description, images: [image] },
    robots: { index: false, follow: false },
  };
}

export default async function SharedPredictionPage({ params }: Props) {
  const { token } = await params;
  const card = await shareCardByToken(token);
  if (!card) notFound();
  return (
    <main className="share-page">
      <p className="share-brand"><span className="wordmark-dot" /> DIKAMAHA</p>
      {/* eslint-disable-next-line @next/next/no-img-element -- PNG generado
          en tiempo de ejecución por `ImageResponse`; el optimizador de
          `next/image` no aporta nada sobre una ruta que ya sirve un PNG fijo
          e inmutable, y añadiría una dependencia de configuración de dominios
          para una página que debe funcionar sin nada más. */}
      <img
        className="share-card-image"
        src={`/s/${token}/image`}
        width={1080}
        height={1350}
        alt={`Predicción pre-match de ${card.homeName} contra ${card.awayName}`}
      />
      <p className="share-hint">
        Mantén pulsada la imagen para guardarla o compartirla.
      </p>
      <p className="share-disclosure">
        Probabilidades calculadas antes del inicio del partido. Contenido
        analítico: no es asesoría financiera ni una recomendación de apuesta.
      </p>
    </main>
  );
}
