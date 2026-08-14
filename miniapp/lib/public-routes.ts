/**
 * Rutas que se sirven sin sesión de Telegram.
 *
 * Hoy sólo las tarjetas compartidas (`/s/<token>`): existen precisamente para
 * que alguien de fuera las abra, así que el portero de `TelegramAuth` y el
 * marco de navegación de `AppShell` no deben aplicarse ahí. Ambos componentes
 * consultan esta única función para que no puedan discrepar -si uno se saltara
 * el portero y el otro no, el visitante vería la barra de navegación de una
 * aplicación en la que no ha entrado-.
 *
 * Vive aparte de los componentes para poder probarse sin montar React.
 */
export function isPublicRoute(pathname: string): boolean {
  return pathname === "/s" || pathname.startsWith("/s/");
}
