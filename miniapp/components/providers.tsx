"use client";

import { createSyncStoragePersister } from "@tanstack/query-sync-storage-persister";
import { QueryClient, useQueryClient } from "@tanstack/react-query";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { api } from "@/lib/client-api";
import { isPublicRoute } from "@/lib/public-routes";
import { detectContext, type RuntimeContext } from "@/lib/runtime-context";

type User = {
  id: number;
  firstName: string;
  username?: string;
  role?: "user" | "admin";
  plan?: string;
};

/**
 * Traduce los códigos de acceso del servidor a algo accionable.
 *
 * Sin esto la pantalla de error mostraba el código crudo -`access_denied`- que
 * no le dice al usuario si debe pedir el alta o dejar de intentarlo.
 */
function accessMessage(code: string): string {
  if (code === "access_pending") {
    return "Tu cuenta está registrada y esperando aprobación. "
      + "Escribe al grupo de soporte para que un administrador la active.";
  }
  if (code === "access_blocked") {
    return "Tu acceso está bloqueado. Contacta con un administrador si crees "
      + "que se trata de un error.";
  }
  if (code === "miniapp_disabled") {
    return "DIKAMAHA está temporalmente fuera de servicio.";
  }
  return code;
}

/**
 * Dónde corre la aplicación, resuelto una vez para toda la interfaz.
 *
 * `null` mientras no se sabe, que es exactamente el primer render: este árbol
 * también se renderiza en el servidor, donde no hay `window` que interrogar.
 * Devolver "web" por defecto haría que el HTML servido y el cliente
 * discreparan dentro de Telegram y React descartaría la hidratación entera
 * -el mismo fallo que ya obligó a mover la sesión recordada a un efecto-.
 */
const RuntimeContextValue = createContext<RuntimeContext | null>(null);

export function useRuntimeContext(): RuntimeContext | null {
  return useContext(RuntimeContextValue);
}

/**
 * Libera el bloqueo de zoom fuera de Telegram.
 *
 * `app/layout.tsx` fija `maximum-scale=1` por un bug real del WebView de iOS,
 * y ese `viewport` es estático: se sirve igual a los dos contextos. En un
 * navegador de verdad no hay WebView que proteger y sí una barrera de
 * accesibilidad -impide el pellizco-, así que se retira en cuanto se sabe que
 * no estamos dentro de Telegram. Después del primer pintado, que es cuando el
 * bug de escala inicial ya no puede ocurrir.
 */
function unlockViewportZoom(): void {
  const meta = document.querySelector('meta[name="viewport"]');
  if (!meta) return;
  meta.setAttribute("content", "width=device-width, initial-scale=1, viewport-fit=cover");
}
type AuthState = {
  user: User | null;
  csrfToken: string;
  loading: boolean;
  error: string | null;
  retry(): void;
};

const AuthContext = createContext<AuthState | null>(null);

/**
 * Rastro de la última sesión válida. No es una credencial.
 *
 * Sólo guarda lo necesario para pintar el marco de la aplicación -el nombre en
 * la cabecera- sin esperar a que `/api/session/me` responda. Quien autentica
 * sigue siendo exclusivamente la cookie firmada: si ya no vale, la sesión
 * optimista se descarta y se vuelve al alta por Telegram.
 */
const LAST_SESSION_KEY = "dikamaha-miniapp-last-user";

function rememberedUser(): User | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(LAST_SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<User>;
    return typeof value.id === "number" && typeof value.firstName === "string"
      ? { id: value.id, firstName: value.firstName, username: value.username }
      : null;
  } catch {
    return null;
  }
}

function rememberUser(user: User | null): void {
  if (typeof window === "undefined") return;
  try {
    if (user) window.localStorage.setItem(LAST_SESSION_KEY, JSON.stringify(user));
    else window.localStorage.removeItem(LAST_SESSION_KEY);
  } catch {
    // Un `localStorage` lleno o bloqueado sólo cuesta el arranque optimista.
  }
}

function startPath(startParam?: string): string | null {
  if (!startParam) return null;
  if (startParam === "live") return "/live";
  if (startParam === "models") return "/models";
  if (startParam === "explore") return "/explore";
  if (startParam === "matches") return "/explore/matches";
  if (startParam === "players") return "/explore/teams";
  if (startParam === "status") return "/status";
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

/**
 * Identidad de la caché persistida. Cambiarla descarta lo guardado en disco.
 *
 * Se sube a mano cuando cambia la *forma* de un payload cacheado, no en cada
 * despliegue: atarla al commit vaciaría la caché de todos los usuarios cada
 * vez que se publica algo, que es exactamente lo que esta persistencia existe
 * para evitar. Un despliegue que no toca los contratos debe conservar lo que
 * el usuario ya tenía pintado.
 */
const CACHE_SCHEMA_VERSION = "v1";

// Un día. Los catálogos se revalidan en segundo plano nada más rehidratar, así
// que una entrada vieja sólo se usa para pintar de inmediato mientras llega la
// versión fresca; no se muestra como si fuera actual.
const PERSISTED_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => {
    const client = new QueryClient({
      defaultOptions: {
        queries: {
          retry: 1,
          // Defecto pensado para el catálogo live, lo único que envejece rápido.
          staleTime: 15_000,
          refetchOnWindowFocus: true,
          // `gcTime` tiene que superar la ventana de persistencia: React Query
          // descarta al rehidratar cualquier entrada más vieja que este valor,
          // y con el defecto de 5 minutos la caché guardada en disco se
          // tiraría entera en cuanto la Mini App estuviera cerrada un rato
          // -es decir, siempre-.
          gcTime: PERSISTED_CACHE_MAX_AGE_MS,
        },
        mutations: { retry: 0 },
      },
    });
    // Por prefijo de clave y no en cada `useQuery`: la misma consulta de ligas
    // aparece en cinco pantallas y el catálogo de próximos en dos, así que
    // repetir el `staleTime` en cada llamada se desincronizaría a la primera.
    // Sin esto, todo heredaba los 15s del defecto y la caché rehidratada se
    // revalidaba entera al abrir aunque nada hubiera cambiado.
    for (const key of [["upcoming"], ["high-probability"]]) {
      client.setQueryDefaults(key, { staleTime: 300_000 });
    }
    for (const key of [["explorer-leagues"], ["explorer-dates"], ["models"]]) {
      // Inventario de modelos y catálogos de navegación: cambian con un
      // despliegue o con el calendario, nunca dentro de una sesión.
      client.setQueryDefaults(key, { staleTime: 3_600_000 });
    }
    return client;
  });

  // Sin persistencia el `QueryClient` vive sólo en memoria, así que cerrar la
  // Mini App borraba todo y cada apertura arrancaba con cero datos aunque los
  // mismos bytes se hubieran descargado segundos antes. Guardarla en
  // `localStorage` permite pintar la última vista al instante y revalidar por
  // detrás, que es lo que elimina la sensación de "carga completa" al reabrir.
  const [persister] = useState(() => createSyncStoragePersister({
    // El WebView de Telegram ejecuta esto en cliente, pero el componente
    // también se renderiza en el servidor, donde `window` no existe.
    storage: typeof window === "undefined" ? undefined : window.localStorage,
    key: "dikamaha-miniapp-cache",
  }));

  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister,
        maxAge: PERSISTED_CACHE_MAX_AGE_MS,
        buster: CACHE_SCHEMA_VERSION,
        dehydrateOptions: {
          shouldDehydrateQuery: (query) => (
            query.state.status === "success"
            // El avance del barrido describe una operación en curso en el
            // servidor: rehidratarlo mostraría una barra de progreso congelada
            // de una sesión anterior.
            && query.queryKey[0] !== "live-progress"
          ),
        },
      }}
    >
      <TelegramAuth>{children}</TelegramAuth>
    </PersistQueryClientProvider>
  );
}

function TelegramAuth({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  // Una tarjeta compartida existe para que la abra alguien sin cuenta. El
  // portero se salta ahí -no se intenta autenticar y no se bloquea el
  // contenido-, pero el contexto se sigue proveyendo para que cualquier
  // componente que llame a `useAuth` siga encontrándolo.
  const isPublic = isPublicRoute(pathname);
  const [attempt, setAttempt] = useState(0);
  // Se resuelve en un efecto, no al construir el estado, por lo mismo que la
  // sesión recordada: en el servidor no hay `window`.
  const [context, setContext] = useState<RuntimeContext | null>(null);
  const [state, setState] = useState<Omit<AuthState, "retry">>({
    user: null,
    csrfToken: "",
    loading: true,
    error: null,
  });
  const retry = useCallback(() => setAttempt((value) => value + 1), []);

  // Arrancar optimista cuando ya hubo una sesión: la interfaz se pinta y las
  // consultas de datos salen en paralelo con `/api/session/me` en vez de
  // después. Antes nada se renderizaba hasta que la autenticación terminaba,
  // así que la primera petición de datos ni siquiera había salido cuando el
  // usuario ya llevaba un round-trip esperando. Sin sesión previa no hay nada
  // que adelantar: esas consultas sólo devolverían 401.
  //
  // Va en un efecto y no en el estado inicial porque este componente también se
  // renderiza en el servidor, donde `localStorage` no existe: leerlo al
  // construir el estado hacía que el HTML servido dijera "Usuario" y el cliente
  // "Marco", y React descartaba la hidratación entera por el desajuste.
  useEffect(() => {
    const remembered = rememberedUser();
    if (!remembered) return;
    setState((current) => (
      // Si la sesión ya se confirmó de verdad, no degradarla a la recordada.
      current.csrfToken ? current : { ...current, user: remembered, loading: false }
    ));
  }, [attempt]);

  // Fuera del efecto de autenticación porque también hace falta en las rutas
  // públicas, donde ese efecto no llega a correr: una tarjeta compartida se
  // abre casi siempre desde un navegador.
  useEffect(() => { setContext(detectContext()); }, []);

  useEffect(() => {
    if (isPublic) return;
    let active = true;
    const context = detectContext();
    const webApp = window.Telegram?.WebApp;
    // Dentro de Telegram manda el contenedor; fuera, la preferencia del
    // sistema. El atributo escrito es el mismo en los dos casos, así que
    // `globals.css` no distingue contextos: su bloque `[data-tg-theme="light"]`
    // ya servía para esto sin saberlo.
    const media = typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-color-scheme: light)")
      : null;
    const applyTheme = () => {
      document.documentElement.dataset.tgTheme = context === "telegram"
        ? webApp?.colorScheme ?? "dark"
        : media?.matches ? "light" : "dark";
    };
    applyTheme();
    if (context === "telegram") webApp?.onEvent("themeChanged", applyTheme);
    else {
      media?.addEventListener("change", applyTheme);
      unlockViewportZoom();
    }
    async function authenticate() {
      try {
        const existing = await fetch("/api/session/me", {
          cache: "no-store",
          credentials: "include",
        });
        if (existing.ok) {
          const payload = await existing.json() as { user: User; csrfToken: string };
          rememberUser(payload.user);
          if (active) setState({ user: payload.user, csrfToken: payload.csrfToken, loading: false, error: null });
          return;
        }
        // La cookie ya no vale: lo pintado era optimista y hay que rehacer el
        // alta. Se vuelve a la pantalla de arranque para no dejar a la vista
        // una interfaz cuyas consultas están devolviendo 401.
        if (active) setState((current) => ({ ...current, loading: true, error: null }));
        // Fuera de Telegram no hay nada que reintentar en silencio: el pase lo
        // emite el Login Widget, que vive en una pantalla propia. Se manda ahí
        // en vez de mostrar un error, porque no es un fallo sino la ausencia
        // esperada de sesión.
        if (context === "web") {
          rememberUser(null);
          router.replace("/login");
          return;
        }
        if (!webApp?.initData) throw new Error("Abre DIKAMAHA desde Telegram.");
        webApp.ready();
        webApp.expand();
        const payload = await api<{ user: User; csrfToken: string; startParam?: string }>(
          "/api/session/telegram",
          { method: "POST", body: JSON.stringify({ initData: webApp.initData }) },
        );
        const confirmed = await fetch("/api/session/me", {
          cache: "no-store",
          credentials: "include",
        });
        if (!confirmed.ok) {
          throw new Error("Telegram no pudo conservar la sesión segura. Cierra y vuelve a abrir la Mini App.");
        }
        const confirmedPayload = await confirmed.json() as { user: User; csrfToken: string };
        rememberUser(confirmedPayload.user);
        if (active) {
          setState({
            user: confirmedPayload.user,
            csrfToken: confirmedPayload.csrfToken,
            loading: false,
            error: null,
          });
          // Hubo que reautenticar, así que cualquier consulta lanzada de forma
          // optimista falló con 401 y su error quedó cacheado. Sin esto el
          // usuario vería paneles de error sobre una sesión ya válida.
          void queryClient.invalidateQueries();
          const target = startPath(payload.startParam);
          if (target) router.replace(target);
        }
      } catch (error) {
        rememberUser(null);
        if (active) setState({
          user: null,
          csrfToken: "",
          loading: false,
          error: error instanceof Error
            ? accessMessage(error.message)
            : "No fue posible iniciar sesión.",
        });
      }
    }
    void authenticate();
    return () => {
      active = false;
      webApp?.offEvent("themeChanged", applyTheme);
      media?.removeEventListener("change", applyTheme);
    };
  }, [attempt, router, queryClient, isPublic]);

  return (
    <RuntimeContextValue.Provider value={context}>
      <AuthContext.Provider value={{ ...state, retry }}>
        {isPublic ? children
          : state.loading ? <LaunchScreen />
          : state.error ? <AuthError message={state.error} retry={retry} />
          : children}
      </AuthContext.Provider>
    </RuntimeContextValue.Provider>
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
