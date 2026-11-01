const LINKS = [
  { id: 'today', label: 'Today' },
  { id: 'calendar', label: 'Calendar' },
  { id: 'collection', label: 'Collection' },
  { id: 'settings', label: 'Settings' },
];

export default function Nav({ current }) {
  return (
    <nav className="nav" aria-label="Main">
      <ul>
        {LINKS.map((l) => (
          <li key={l.id}>
            <a href={`#/${l.id}`} aria-current={current === l.id ? 'page' : undefined}>
              {l.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
