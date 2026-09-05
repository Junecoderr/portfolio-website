export default function SectionHeading({ title, lead }) {
  return (
    <div className="section-heading" data-reveal="1">
      <h2 className="section-title">{title}</h2>
      {lead ? <p className="section-lead">{lead}</p> : null}
    </div>
  );
}
