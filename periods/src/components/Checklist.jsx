import { CATEGORIES } from '../data/routines.js';

export default function Checklist({ items, done, onToggle }) {
  return (
    <div className="checklist">
      {Object.entries(CATEGORIES).map(([cat, label]) => {
        const group = items.filter((i) => i.category === cat);
        if (!group.length) return null;
        return (
          <section key={cat} className="checklist__group" aria-labelledby={`cat-${cat}`}>
            <h2 id={`cat-${cat}`} className="eyebrow">
              {label}
            </h2>
            <ul>
              {group.map((item) => (
                <li key={item.id}>
                  <label className="check">
                    <input type="checkbox" checked={done.includes(item.id)} onChange={() => onToggle(item.id)} />
                    <span className="check__text">
                      <span className="check__title">{item.title}</span>
                      {item.why && <span className="check__why">{item.why}</span>}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}
