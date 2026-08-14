"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, useEffect } from "react";

import { useAuth } from "@/components/providers";
import { isPublicRoute } from "@/lib/public-routes";

const navigation = [
  { href: "/", icon: "⌂", label: "Inicio" },
  { href: "/live", icon: "●", label: "En vivo" },
  { href: "/predictions", icon: "◫", label: "Predicciones" },
  { href: "/mayor-probabilidad", icon: "▲", label: "Mayor prob." },
  { href: "/explore", icon: "⌘", label: "Explorar" },
  { href: "/historial", icon: "✓", label: "Aciertos" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user } = useAuth();

  useEffect(() => {
    const back = window.Telegram?.WebApp.BackButton;
    if (!back) return;
    const onBack = () => router.back();
    if (pathname === "/") back.hide();
    else {
      back.show();
      back.onClick(onBack);
    }
    return () => back.offClick(onBack);
  }, [pathname, router]);

  // Una tarjeta compartida la abre alguien que no tiene cuenta: darle la barra
  // superior y la navegación de la aplicación le ofreceria seis destinos que
  // no puede abrir. El hook de arriba ya corrió, así que el orden de hooks no
  // cambia entre renders.
  if (isPublicRoute(pathname)) return <>{children}</>;

  return (
    <div className="app-frame">
      <header className="topbar">
        <Link href="/" className="wordmark" aria-label="Ir al inicio">
          <span className="wordmark-dot" /> DIKAMAHA
        </Link>
        <Link href="/settings" className="user-chip" aria-label="Abrir ajustes">
          <span className="status-dot" />
          <span className="user-chip-name">{user?.firstName ?? "Usuario"}</span>
        </Link>
      </header>
      <main className="content">{children}</main>
      <nav className="bottom-nav" aria-label="Navegación principal">
        {navigation.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link key={item.href} href={item.href} className={active ? "nav-item active" : "nav-item"}>
              <span aria-hidden="true">{item.icon}</span>
              <small>{item.label}</small>
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
