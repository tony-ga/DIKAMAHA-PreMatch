import Link from "next/link";

import { PageHeader } from "@/components/ui";

const sections = [
  ["Próximos", "Filtra por liga o fecha y abre predicciones oficiales y mercados de equipo shadow.", "/upcoming"],
  ["En vivo", "Sigue marcador, Markov Live, residual Hawkes, combinación y próximo evento.", "/live"],
  ["Centro de partidos", "Consulta contexto, play-by-play y estadísticas históricas por periodo.", "/explore/matches"],
  ["Equipos y jugadores", "Busca equipos, revisa rosters y perfiles publicados.", "/explore/teams"],
  ["Modelos", "Distingue modelos oficiales de experimentos shadow no promovidos.", "/models"],
  ["Alertas", "Crea reglas con cooldown y deduplicación; los mercados se identifican como experimentales.", "/subscriptions"],
] as const;

export default function HelpPage() {
  return (
    <>
      <PageHeader eyebrow="AYUDA" title="Cómo usar DIKAMAHA" />
      <div className="stack">
        {sections.map(([title, description, href]) => <Link className="help-row" href={href} key={href}><div><h3>{title}</h3><p>{description}</p></div><b>→</b></Link>)}
        <div className="notice">DIKAMAHA publica probabilidades analíticas, no cuotas, stakes ni ejecución de apuestas. Los datos de contexto no modifican modelos sin una fase causal validada.</div>
      </div>
    </>
  );
}
