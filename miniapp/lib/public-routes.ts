/**
 * Rutas que se sirven sin sesión.
 *
 * Dos casos, por razones distintas. Las tarjetas compartidas (`/s/<token>`)
 * existen precisamente para que alguien de fuera las abra. `/login` es la
 * pantalla donde se consigue la sesión: exigirla ahí sería un bucle.
 *
 * El portero de `TelegramAuth` y el marco de navegación de `AppShell`
 * consultan esta única función para que no puedan discrepar -si uno se saltara
 * el portero y el otro no, el visitante vería la barra de navegación de una
 * aplicación en la que no ha entrado-.
 *
 * Vive aparte de los componentes para poder probarse sin montar React.
 */
export function isPublicRoute(pathname: string): boolean {
  if (pathname === "/login") return true;
  return pathname === "/s" || pathname.startsWith("/s/");
}
