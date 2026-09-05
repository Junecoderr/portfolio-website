/** Big ghosted section number with the small uppercase title on top. */
export default function SectionHeading({ number, title, align = 'left' }) {
  return (
    <div className={`section-heading is-${align}`} data-reveal="1">
      <span className="section-number" aria-hidden="true">{number}</span>
      <h2 className="section-title">{title}</h2>
    </div>
  );
}
