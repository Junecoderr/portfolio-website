const TOOLS = ['Splunk', 'Elastic', 'Sigma', 'Zeek', 'Suricata', 'Velociraptor', 'YARA', 'MISP', 'Wireshark', 'Vault', 'OpenSSL'];

export default function Tooling() {
  return (
    <section className="section">
      <div className="section-content">
        <div className="tooling-label reveal">Tooling I work in</div>
        <ul className="tooling-list reveal">
          {TOOLS.map((tool) => (
            <li key={tool} className="tooling-item">
              {tool}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
