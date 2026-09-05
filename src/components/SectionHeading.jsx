/** Big ghosted section number, small uppercase title, optional one-line lead. */
export default function SectionHeading({ number, title, align = 'left', lead }) {
  return (
    <div className={`section-heading is-${align}`} data-reveal="1">
      <span className="section-number" aria-hidden="true">{number}</span>
      <div className="section-heading-text">
        <h2 className="section-title">{title}</h2>
        {lead ? <p className="section-lead">{lead}</p> : null}
      </div>
    </div>
  );
}
