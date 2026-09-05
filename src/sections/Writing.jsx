import SectionHeading from '../components/SectionHeading.jsx';
import posts from '../data/writing.generated.json';
import { ArrowUpRight } from '../components/Icons.jsx';

/** Latest Medium posts, fetched at build time. Renders nothing when the feed is empty. */
export default function Writing() {
  if (!posts.length) return null;
  return (
    <section id="writing" className="section">
      <div className="container">
        <SectionHeading number="05" title="Writing" lead="Recent posts on Medium." />
        <ul className="writing-list" data-reveal="1">
          {posts.map((p) => (
            <li key={p.url} className="writing-row">
              <a href={p.url} target="_blank" rel="noopener noreferrer">
                <span className="writing-title">{p.title}</span>
                <span className="writing-meta">{p.date} <ArrowUpRight size={14} /></span>
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
