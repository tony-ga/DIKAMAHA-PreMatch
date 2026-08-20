"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Botón oficial "Log in with Telegram".
 *
 * El widget no es un componente sino un `<script>` que se sustituye a sí mismo
 * por un iframe, así que hay que inyectarlo en el DOM y no renderizarlo con
 * React. Entrega el resultado en una función global; se usa `data-onauth`
 * -y no `data-auth-url`- para que el payload firmado llegue por POST en lugar
 * de viajar en la barra de direcciones, donde quedaría en el historial del
 * navegador y en cualquier registro intermedio.
 */
declare global {
  interface Window {
    onTelegramAuth?: (user: Record<string, unknown>) => void;
  }
}

type Status = "idle" | "working" | "failed" | "denied";

const MESSAGES: Record<string, string> = {
  access_pending: "Tu cuenta está registrada y esperando aprobación. "
    + "Escribe al grupo de soporte para que un administrador la active.",
  access_blocked: "Tu acceso está bloqueado. Contacta con un administrador si "
    + "crees que se trata de un error.",
  miniapp_disabled: "DIKAMAHA está temporalmente fuera de servicio.",
  telegram_authentication_failed: "No pudimos verificar tu cuenta de Telegram. "
    + "Inténtalo de nuevo.",
};

export function LoginWidget({ botUsername }: { botUsername: string }) {
  const slot = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!botUsername || !slot.current) return;
    window.onTelegramAuth = async (user) => {
      setStatus("working");
      try {
        const response = await fetch("/api/session/web", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(user),
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({})) as { error?: string };
          const code = payload.error ?? "telegram_authentication_failed";
          setStatus(code.startsWith("access_") ? "denied" : "failed");
          setMessage(MESSAGES[code] ?? MESSAGES.telegram_authentication_failed);
          return;
        }
        // Recarga completa en vez de `router.push`: el portero de sesión vive
        // en un efecto que ya corrió y decidió que no había sesión. Volver a
        // entrar por la puerta principal lo hace releer la cookie recién
        // puesta, en lugar de tener que enseñarle a rehacer su decisión desde
        // fuera.
        window.location.assign("/");
      } catch {
        setStatus("failed");
        setMessage(MESSAGES.telegram_authentication_failed);
      }
    };
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.async = true;
    script.setAttribute("data-telegram-login", botUsername);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-radius", "12");
    script.setAttribute("data-userpic", "false");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    const host = slot.current;
    host.appendChild(script);
    return () => {
      host.replaceChildren();
      delete window.onTelegramAuth;
    };
  }, [botUsername]);

  if (!botUsername) {
    return (
      <p className="muted">
        El acceso web todavía no está configurado en este servicio. Abre
        DIKAMAHA desde Telegram.
      </p>
    );
  }

  return (
    <div className="login-actions">
      <div ref={slot} aria-label="Entrar con Telegram" />
      {status === "working" ? <p className="muted">Verificando tu cuenta…</p> : null}
      {message ? <p className={status === "denied" ? "muted" : "state-error"}>{message}</p> : null}
    </div>
  );
}
