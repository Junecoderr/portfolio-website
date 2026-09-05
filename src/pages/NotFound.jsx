import PageShell from './PageShell.jsx';

export default function NotFound() {
  return (
    <PageShell title="Nothing at this address." lead="The page may have moved, or the link was mistyped.">
      <ul className="subpage-links">
        <li><a href="/">Home</a></li>
        <li><a href="/#work">Case studies</a></li>
        <li><a href="/resume">Resume</a></li>
        <li><a href="/#contact">Contact</a></li>
      </ul>
    </PageShell>
  );
}
