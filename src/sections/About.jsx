import { BIO_FACTS, RECOGNITION } from '../data/content.js';

export default function About() {
  return (
    <main id="about" className="page-fade route-main">
      <section className="section-pad">
        <div className="container">
          <span className="mono-label">About</span>
          <h1 className="route-title wide">
            I work the two ends of the same problem: catching intrusions, and making them expensive.
          </h1>
        </div>
      </section>

      <section className="section-pad-b">
        <div className="container about-grid">
          <div data-reveal="1" className="about-copy">
            <p>
              Six years in security operations, four of them writing cryptography for production systems. I started on a
              tier-two console triaging endpoint alerts and moved into detection engineering when it became clear the rules
              were the product, not the console.
            </p>
            <p>
              The cryptography came later and by necessity. Half the incidents I worked traced back to a primitive used
              wrongly: a nonce reused, a key stored beside the data it protected, a comparison that leaked timing. Learning to
              build them correctly was the fastest way to stop reading about them at 3am.
            </p>
            <p>
              Today I split my time between detection-as-code programmes and protocol implementation reviews. I write Rust and
              Python, read Go, and prefer a specification to a diagram.
            </p>
          </div>
          <div data-reveal="1" className="fact-list">
            {BIO_FACTS.map((f) => (
              <div key={f.k} className="fact-row">
                <span className="mono-label">{f.k}</span>
                <span className="fact-value">{f.v}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="section-pad-b">
        <div className="container">
          <div data-reveal="1" className="section-head">
            <h2 className="section-title">Recognition</h2>
            <span className="mono-label">As of 30 Jun 2026</span>
          </div>
          {RECOGNITION.map((g) => (
            <div key={g.index} data-reveal="1" className="recognition-row">
              <div className="recognition-head">
                <span className="mono-index">{g.index}</span>
                <h3 className="h3-title">{g.title}</h3>
              </div>
              <div className="recognition-items">
                {g.items.map((i) => (
                  <div key={i.name} className="recognition-item">
                    <span className="recognition-item-name">{i.name}</span>
                    <span className="mono-label soft">{i.detail}</span>
                    <span className="mono-label align-end">{i.meta}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
