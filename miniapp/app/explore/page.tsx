import { FeatureCard, PageHeader } from "@/components/ui";

export default function ExplorePage() {
  return (
    <>
      <PageHeader eyebrow="TODAS LAS FUNCIONES DEL BOT" title="Centro de datos" />
      <div className="feature-grid">
        <FeatureCard href="/explore/matches" eyebrow="PLAY-BY-PLAY + STATS" title="Partidos históricos" description="Navega por liga y fecha. Abre contexto, eventos clave, timeline completo y estadísticas 1T, 2T y total." />
        <FeatureCard href="/explore/teams" eyebrow="ROSTER + PERFILES" title="Equipos y jugadores" description="Busca equipos, consulta plantillas y abre perfiles y acumulados publicados." />
        <FeatureCard href="/models" eyebrow="INVENTARIO REAL" title="Modelos en operación" description="Consulta las capas oficiales y shadow realmente cargadas por la API." />
        <FeatureCard href="/status" eyebrow="CONEXIÓN" title="Estado del sistema" description="Verifica sesión, API DIKAMAHA y versión de contrato sin exponer secretos." />
        <FeatureCard href="/help" eyebrow="GUÍA" title="Cómo usar DIKAMAHA" description="Mapa de funciones, significado de modelos y límites de uso responsable." />
        <FeatureCard href="/settings" eyebrow="TELEGRAM" title="Cuenta y favoritos" description="Consulta tu identidad validada y los partidos, equipos y ligas guardados." />
      </div>
    </>
  );
}
