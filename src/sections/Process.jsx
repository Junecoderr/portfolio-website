const STEPS = [
  {
    num: '01',
    title: 'Triage',
    body: 'Signal separated from noise before it reaches an owner. Severity, scope, and a first verdict inside the hour.',
    stripColor: 'rgba(124,60,237,.12)',
  },
  {
    num: '02',
    title: 'Analysis',
    body: 'Root cause and blast radius established from evidence — host artefacts, network sessions, and key material touched.',
    stripColor: 'rgba(255,47,190,.12)',
  },
  {
    num: '03',
    title: 'Containment',
    body: 'Access cut, keys rotated, detections written so the same path fires an alert the next time it is walked.',
    stripColor: 'rgba(0,217,255,.12)',
  },
];

export default function Process() {
  return (
    <section id="process" className="section">
      <div className="process-grid">
        <div className="process-rail-col">
          <div className="process-sticky">
            <div className="section-kicker reveal">
              <span>03 — Process</span>
              <span data-step-count>1 / 3</span>
            </div>
            <h2 className="section-title reveal" style={{ maxWidth: '18ch' }}>
              How an incident moves through my hands.
            </h2>
            <p className="process-lead reveal">Three stages, each with an artefact at the end of it. Nothing advances on a hunch.</p>
            <div className="process-rail-track">
              <div className="process-rail-fill" data-rail />
            </div>
            <div className="process-rail-labels">
              <span>Triage</span>
              <span>Analysis</span>
              <span>Containment</span>
            </div>
          </div>
        </div>
        <div className="process-steps">
          {STEPS.map((step, i) => (
            <article key={step.num} data-step={i} className="process-step">
              <div className="process-step-head">
                <span className="process-step-num">{step.num}</span>
                <h3 className="process-step-title">{step.title}</h3>
              </div>
              <p className="process-step-body">{step.body}</p>
              <div
                aria-hidden="true"
                className="process-step-image"
                style={{ backgroundImage: `repeating-linear-gradient(112deg, ${step.stripColor} 0 2px, transparent 2px 12px)` }}
              >
                <span className="strip-label">strip image · 2066 × 168</span>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
