import Link from "next/link";

export default function NotFound() {
  return (
    <section className="state-panel">
      <div className="state-icon">404</div>
      <h2>Vista no encontrada</h2>
      <Link className="primary-button" href="/">Volver al panel</Link>
    </section>
  );
}
