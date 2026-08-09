"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { api } from "@/lib/client-api";

type User = { id: number; firstName: string; username?: string };
type AuthState = {
  user: User | null;
  csrfToken: string;
  loading: boolean;
  error: string | null;
  retry(): void;
};

const AuthContext = createContext<AuthState | null>(null);

function startPath(startParam?: string): string | null {
  if (!startParam) return null;
  if (startParam === "live") return "/live";
  if (startParam === "models") return "/models";
  const live = /^fixture_(\d+)_([A-Za-z0-9_-]+)$/.exec(startParam);
  const prediction = /^prediction_(\d+)_([A-Za-z0-9_-]+)_(\d+)_(\d+)_(\d+)$/.exec(startParam);
  const match = live ?? prediction;
  if (!match) return null;
  try {
    const encoded = match[2].replaceAll("-", "+").replaceAll("_", "/");
    const league = atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "="));
    if (live) return `/live/${match[1]}?league=${encodeURIComponent(league)}`;
    const kickoff = new Date(Number(match[5]) * 1000).toISOString();
    return `/predictions/${match[1]}?league=${encodeURIComponent(league)}&home=${match[3]}&away=${match[4]}&kickoff=${encodeURIComponent(kickoff)}`;
  } catch {
    return null;
  }
}

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient({
    defaultOptions: {
      queries: { retry: 1, staleTime: 15_000, refetchOnWindowFocus: true },
      mutations: { retry: 0 },
    },
  }));
  return (
    <QueryClientProvider client={queryClient}>
      <TelegramAuth>{children}</TelegramAuth>
    </QueryClientProvider>
  );
}

function TelegramAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Omit<AuthState, "retry">>({
    user: null,
    csrfToken: "",
    loading: true,
    error: null,
  });
  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    const webApp = window.Telegram?.WebApp;
    const applyTheme = () => {
      document.documentElement.dataset.tgTheme = webApp?.colorScheme ?? "dark";
    };
    applyTheme();
    webApp?.onEvent("themeChanged", applyTheme);
    async function authenticate() {
      setState((current) => ({ ...current, loading: true, error: null }));
      try {
        const existing = await fetch("/api/session/me", { cache: "no-store" });
        if (existing.ok) {
          const payload = await existing.json();
          if (active) setState({ user: payload.user, csrfToken: payload.csrfToken, loading: false, error: null });
          return;
        }
        if (!webApp?.initData) throw new Error("Abre DIKAMAHA desde Telegram.");
        webApp.ready();
        webApp.expand();
        const payload = await api<{ user: User; csrfToken: string; startParam?: string }>(
          "/api/session/telegram",
          { method: "POST", body: JSON.stringify({ initData: webApp.initData }) },
        );
        if (active) {
          setState({ user: payload.user, csrfToken: payload.csrfToken, loading: false, error: null });
          const target = startPath(payload.startParam);
          if (target) router.replace(target);
        }
      } catch (error) {
        if (active) setState({
          user: null,
          csrfToken: "",
          loading: false,
          error: error instanceof Error ? error.message : "No fue posible iniciar sesión.",
        });
      }
    }
    void authenticate();
    return () => {
      active = false;
      webApp?.offEvent("themeChanged", applyTheme);
    };
  }, [attempt, router]);

  return (
    <AuthContext.Provider value={{ ...state, retry }}>
      {state.loading ? <LaunchScreen /> : state.error ? <AuthError message={state.error} retry={retry} /> : children}
    </AuthContext.Provider>
  );
}

function LaunchScreen() {
  return (
    <main className="launch-screen" aria-live="polite">
      <div className="brand-mark">DK</div>
      <p className="eyebrow">DIKAMAHA LIVE INTELLIGENCE</p>
      <h1>Preparando tu panel</h1>
      <div className="scan-line" />
    </main>
  );
}

function AuthError({ message, retry }: { message: string; retry(): void }) {
  return (
    <main className="launch-screen">
      <div className="brand-mark warning">!</div>
      <p className="eyebrow">ACCESO SEGURO</p>
      <h1>No pudimos validar la sesión</h1>
      <p className="muted">{message}</p>
      <button className="primary-button" onClick={retry}>Reintentar</button>
    </main>
  );
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("AuthContext unavailable");
  return context;
}
