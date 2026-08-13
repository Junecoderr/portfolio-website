import ScanGrid from '../components/backgrounds/ScanGrid.jsx';

export default function Quote() {
  return (
    <section className="section-quote">
      <div className="section-bg">
        <ScanGrid accent="magenta" opacity={0.12} />
      </div>
      <div className="quote-scrim" aria-hidden="true" />
      <div className="quote-content">
        <div className="quote-kicker reveal">(02)</div>
        <blockquote className="quote-text reveal">&ldquo;Security is a process, not a product.&rdquo;</blockquote>
        <div className="quote-attribution reveal">Bruce Schneier · Secrets and Lies</div>
      </div>
    </section>
  );
}
