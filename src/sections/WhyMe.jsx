import PointerBloom from '../components/backgrounds/PointerBloom.jsx';

const REASONS = [
  {
    kicker: '01 / DTN',
    title: 'Detection Engineering',
    body: 'Rules written against adversary behaviour, not signatures that expire the week they ship.',
  },
  {
    kicker: '02 / TRI',
    title: 'Triage Under Load',
    body: 'Every alert reaches a verdict, and every verdict is written down where the next analyst can find it.',
  },
  {
    kicker: '03 / CRY',
    title: 'Applied Cryptography',
    body: 'Key lifecycle, protocol review, and encryption at rest and in transit that survives an audit.',
  },
  {
    kicker: '04 / HNT',
    title: 'Threat Hunting',
    body: 'Hypothesis-led sweeps through the telemetry no dashboard was built to surface.',
  },
  {
    kicker: '05 / IR',
    title: 'Incident Response',
    body: 'Containment first, forensics second, and a timeline the business can actually read.',
  },
];

export default function WhyMe() {
  return (
    <section id="why" className="section">
      <div className="section-bg">
        <PointerBloom />
      </div>
      <div className="section-content">
        <div className="section-kicker reveal">
          <span>01 — Why me</span>
          <span>Five reasons</span>
        </div>
        <h2 className="section-title reveal">The work is detection, evidence, and key discipline — in that order.</h2>
        <div className="why-grid">
          {REASONS.map((reason) => (
            <article key={reason.kicker} className="why-card reveal">
              <div className="why-card-kicker">{reason.kicker}</div>
              <h3 className="why-card-title">{reason.title}</h3>
              <p className="why-card-body">{reason.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
