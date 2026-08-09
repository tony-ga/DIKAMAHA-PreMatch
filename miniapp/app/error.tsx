"use client";

export default function ErrorPage({ reset }: { reset(): void }) {
  return (
    <section className="state-panel">
      <div className="state-icon">!</div>
      <h2>La vista no pudo cargarse</h2>
      <p className="muted">Los modelos no fueron modificados. Puedes reintentar la consulta.</p>
      <button className="primary-button" onClick={reset}>Reintentar</button>
    </section>
  );
}
