import { percentage } from "@/lib/client-api";

type Props = {
  homeName: string;
  awayName: string;
  probabilityHome: number;
  probabilityDraw: number;
  probabilityAway: number;
  expectedHomeGoals: number;
  expectedAwayGoals: number;
  lambdaHome: number;
  lambdaAway: number;
};

function finite(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

export function PredictionAnalytics(props: Props) {
  const outcomes = [
    { label: props.homeName, value: finite(props.probabilityHome) },
    { label: "Empate", value: finite(props.probabilityDraw) },
    { label: props.awayName, value: finite(props.probabilityAway) },
  ];
  const ranked = [...outcomes].sort((left, right) => right.value - left.value);
  const gap = Math.max(0, ranked[0].value - ranked[1].value);
  const entropy = -outcomes.reduce((sum, row) => sum + (row.value > 0 ? row.value * Math.log(row.value) : 0), 0);
  const concentration = Math.max(0, Math.min(1, 1 - entropy / Math.log(3)));
  const totalGoals = finite(props.expectedHomeGoals) + finite(props.expectedAwayGoals);
  const maximumGoals = Math.max(2.5, finite(props.expectedHomeGoals), finite(props.expectedAwayGoals));

  return (
    <>
      <div className="insight-grid">
        <article><span>Resultado con mayor masa</span><strong>{ranked[0].label}</strong><small>{percentage(ranked[0].value)}</small></article>
        <article><span>Separación del líder</span><strong>{percentage(gap)}</strong><small>frente al segundo escenario</small></article>
        <article><span>Concentración 1X2</span><strong>{percentage(concentration)}</strong><small>derivada de entropía, no confianza calibrada</small></article>
        <article><span>Goles esperados</span><strong>{totalGoals.toFixed(2)}</strong><small>suma estructural del partido</small></article>
      </div>

      <section className="expected-goals" aria-label="Comparación de goles esperados">
        <div className="panel-heading"><div><p className="eyebrow">INTENSIDAD DE GOL</p><h3>Goles esperados por equipo</h3></div><span className="chart-legend"><i className="home" />{props.homeName}<i className="away" />{props.awayName}</span></div>
        {[
          { name: props.homeName, value: finite(props.expectedHomeGoals), side: "home" },
          { name: props.awayName, value: finite(props.expectedAwayGoals), side: "away" },
        ].map((row) => (
          <div className="xg-row" key={row.side}>
            <div><span>{row.name}</span><strong>{row.value.toFixed(2)} xG</strong></div>
            <i><b className={row.side} style={{ width: `${Math.min(100, row.value / maximumGoals * 100)}%` }} /></i>
          </div>
        ))}
      </section>

      <div className="prediction-table-wrap">
        <table className="prediction-table">
          <caption>Comparativa matemática del partido</caption>
          <thead><tr><th>Métrica</th><th>{props.homeName}</th><th>{props.awayName}</th></tr></thead>
          <tbody>
            <tr><th>Probabilidad de victoria</th><td>{percentage(props.probabilityHome)}</td><td>{percentage(props.probabilityAway)}</td></tr>
            <tr><th>Goles esperados</th><td>{finite(props.expectedHomeGoals).toFixed(2)}</td><td>{finite(props.expectedAwayGoals).toFixed(2)}</td></tr>
            <tr><th>Intensidad λ</th><td>{finite(props.lambdaHome).toFixed(3)}</td><td>{finite(props.lambdaAway).toFixed(3)}</td></tr>
          </tbody>
        </table>
      </div>
    </>
  );
}
