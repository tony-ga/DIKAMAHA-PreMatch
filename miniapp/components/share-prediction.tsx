"use client";

import { useState } from "react";

import { useAuth, useRuntimeContext } from "@/components/providers";
import { api } from "@/lib/client-api";

type Props = {
  matchId: string;
  leagueSlug: string;
  homeTeamId: string;
  awayTeamId: string;
  kickoffTs: string;
  homeName: string;
  awayName: string;
  homeLogo: string;
  awayLogo: string;
};

type ShareResponse = { token: string; status: string };

/**
 * Congela la tarjeta del partido y entrega su link público.
 *
 * Lo que se difunde es una imagen con marca de agua, no texto: el link abre
 * `/s/<token>`, cuya vista previa en WhatsApp y Telegram es ya el PNG de la
 * tarjeta. Por eso se comparte la URL y no el archivo -un PNG suelto pierde el
 * origen en cuanto lo reenvían, y el link lo conserva-.
 *
 * Tres rutas de salida, en orden de preferencia: la hoja de reenvío nativa de
 * Telegram cuando la Mini App corre dentro del cliente, `navigator.share` en
 * un navegador normal, y el portapapeles como último recurso. El WebView de
 * Telegram en Android no expone `navigator.share`, así que sin la primera vía
 * el caso de uso principal -compartir desde dentro de Telegram- quedaría en el
 * fallback de copiar.
 */
export function SharePrediction(props: Props) {
  const { csrfToken } = useAuth();
  const context = useRuntimeContext();
  const [state, setState] = useState<"idle" | "working" | "copied" | "failed">("idle");
  const [url, setUrl] = useState("");

  async function share() {
    setState("working");
    try {
      const response = await api<ShareResponse>("/api/share", {
        method: "POST",
        body: JSON.stringify({
          matchId: Number(props.matchId),
          leagueSlug: props.leagueSlug,
          homeTeamId: Number(props.homeTeamId),
          awayTeamId: Number(props.awayTeamId),
          kickoffTs: props.kickoffTs,
          homeName: props.homeName,
          awayName: props.awayName,
          homeLogo: props.homeLogo,
          awayLogo: props.awayLogo,
        }),
      }, csrfToken);
      const link = `${window.location.origin}/s/${response.token}`;
      setUrl(link);
      // La hoja de reenvío sólo existe dentro del cliente de Telegram. Fuera,
      // el SDK sigue cargado y `openTelegramLink` sigue definido, pero su
      // mensaje no tiene contenedor que lo reciba: el usuario pulsaría
      // compartir y no pasaría nada.
      const telegram = context === "telegram" ? window.Telegram?.WebApp : undefined;
      if (telegram?.openTelegramLink) {
        telegram.openTelegramLink(
          `https://t.me/share/url?url=${encodeURIComponent(link)}`);
        setState("idle");
        return;
      }
      if (navigator.share) {
        await navigator.share({
          title: `${props.homeName} vs ${props.awayName}`,
          url: link,
        });
        setState("idle");
        return;
      }
      await navigator.clipboard.writeText(link);
      setState("copied");
    } catch (error) {
      // `AbortError` es el usuario cerrando la hoja de compartir, no un fallo.
      if (error instanceof DOMException && error.name === "AbortError") {
        setState("idle");
        return;
      }
      setState("failed");
    }
  }

  return (
    <div className="share-block">
      <button
        type="button"
        className="secondary-button"
        onClick={() => void share()}
        disabled={state === "working"}
      >
        {state === "working" ? "Preparando tarjeta…" : "Compartir tarjeta"}
      </button>
      {state === "copied" ? (
        <small className="share-feedback">Link copiado: {url}</small>
      ) : null}
      {state === "failed" ? (
        <small className="share-feedback share-feedback-error">
          No se pudo preparar la tarjeta. Inténtalo de nuevo.
        </small>
      ) : null}
    </div>
  );
}
