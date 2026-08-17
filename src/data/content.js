export const PROJECTS = [
  {
    id: 'sentinel', index: '01', title: 'Sentinel Grid', tagline: 'Detection-as-code for a 40,000 EPS estate', discipline: 'Threat detection', year: '2025',
    summary: 'A financial-services SOC was running 900 hand-edited correlation rules with no tests and no owner. We rebuilt the ruleset as version-controlled Sigma, gated every change behind unit tests, and cut the alert queue by two thirds without losing a single true positive.',
    facts: [{ k: 'Client', v: 'Tier-1 payments processor' }, { k: 'Role', v: 'Lead detection engineer' }, { k: 'Duration', v: 'Nine months' }, { k: 'Stack', v: 'Sigma · Python · Splunk ES' }],
    metrics: [{ label: 'Rules migrated', value: '912' }, { label: 'False positives', value: '−68%' }, { label: 'Mean triage time', value: '4.2m' }, { label: 'Coverage, ATT&CK', value: '71%' }],
    body: [
      { k: 'Problem', h: 'Rules nobody owned', p: 'Every rule in the estate had been written under incident pressure and never revisited. Twelve analysts had commit rights to a shared console, no rule had a test, and the top ten noisiest detections produced 61% of the queue. Nobody could say what any change would do until it was live.' },
      { k: 'Approach', h: 'Detections as a codebase', p: 'We exported the estate to Sigma, put it in a repository, and required two things of every rule: a documented ATT&CK technique and a test case built from a real telemetry sample. Rules that could not produce a positive sample were retired rather than tuned. The pipeline compiles Sigma to backend queries and deploys on merge.' },
      { k: 'Outcome', h: 'A queue an analyst can finish', p: 'The alert queue fell by two thirds in the first quarter and stayed there. More usefully, rule changes stopped being frightening: the median change now ships in under an hour with a test proving it still fires. Two intrusions found during the programme were caught by rules written that same week.' },
    ],
    timelineTitle: 'Programme phases',
    timeline: [
      { t: 'W1', title: 'Estate inventory', detail: 'Exported and classified all 912 rules by technique, owner and firing volume.' },
      { t: 'W4', title: 'Sigma conversion', detail: 'Automated translation of 640 rules; the remaining 272 rewritten by hand.' },
      { t: 'W9', title: 'Test harness live', detail: 'Every rule gated behind a telemetry-backed unit test in CI.' },
      { t: 'W18', title: 'Noise retirement', detail: '148 rules retired outright after failing to produce a single true positive in twelve months.' },
      { t: 'W31', title: 'Handover', detail: "Runbook and on-call rota transferred to the client's internal team." },
    ],
  },
  {
    id: 'blackout', index: '02', title: 'Blackout', tagline: 'Nine hours from first alert to eradication', discipline: 'Incident response', year: '2025',
    summary: "A logistics operator's build server started signing artefacts it had not built. What looked like a compromised CI runner turned out to be a stolen signing key used from an entirely different network. This is the timeline of that night and what it changed.",
    facts: [{ k: 'Client', v: 'European logistics operator' }, { k: 'Role', v: 'Incident lead' }, { k: 'Duration', v: 'Nine hours, then six weeks' }, { k: 'Classification', v: 'Supply chain · key compromise' }],
    metrics: [{ label: 'Time to containment', value: '3h 14m' }, { label: 'Artefacts revoked', value: '2,481' }, { label: 'Hosts imaged', value: '37' }, { label: 'Downstream impact', value: 'None' }],
    body: [
      { k: 'Detection', h: 'A signature that should not exist', p: 'A Sigma rule written four months earlier fired on a code-signing event with no matching build job. It was a low-severity rule that had never fired in production. The analyst who picked it up escalated within eleven minutes, which is the reason this incident is measured in hours rather than weeks.' },
      { k: 'Investigation', h: 'The key had left the building', p: 'The CI runners were clean. Signing telemetry showed the private key in use from an address in a hosting range the company had never touched, and the earliest use predated the alert by six days. The key had been exfiltrated from a developer laptop through a browser extension with filesystem access.' },
      { k: 'Remediation', h: 'Revoke, rotate, re-sign', p: 'We revoked the intermediate, rotated to hardware-backed signing with an HSM, and re-signed 2,481 artefacts over the following six weeks. No downstream customer consumed a maliciously signed artefact: the attacker was staging, not distributing, when the rule fired.' },
    ],
    timelineTitle: 'Incident timeline · 14 Mar 2025',
    timeline: [
      { t: '21:04', title: 'First alert', detail: 'Unmatched code-signing event raised by SIG-0417. Severity: low.' },
      { t: '21:15', title: 'Escalated', detail: 'Tier-2 analyst confirms no corresponding build job. Incident declared.' },
      { t: '22:40', title: 'Key use localised', detail: 'Signing requests traced to an external hosting range. Six days of prior activity found.' },
      { t: '00:18', title: 'Containment', detail: 'Intermediate certificate revoked. All signing halted estate-wide.' },
      { t: '02:55', title: 'Source identified', detail: 'Malicious browser extension on a developer laptop; key read from disk.' },
      { t: '06:02', title: 'Eradication complete', detail: '37 hosts imaged, extension removed fleet-wide, HSM signing enabled.' },
    ],
  },
  {
    id: 'lattice', index: '03', title: 'Lattice', tagline: 'Hybrid X25519 + ML-KEM across a payments fabric', discipline: 'Post-quantum', year: '2026',
    summary: 'Migrating a payments network to post-quantum key exchange without a flag day. We shipped a hybrid handshake, ran both schemes in parallel for two quarters, and measured every millisecond of the cost.',
    facts: [{ k: 'Client', v: 'Regional card network' }, { k: 'Role', v: 'Cryptography developer' }, { k: 'Duration', v: 'Fourteen months' }, { k: 'Stack', v: 'Rust · liboqs · BoringSSL fork' }],
    metrics: [{ label: 'Endpoints migrated', value: '4,900' }, { label: 'Handshake overhead', value: '+1.8ms' }, { label: 'Classical fallback rate', value: '0.03%' }, { label: 'Downtime', value: '0m' }],
    body: [
      { k: 'Constraint', h: 'No flag day, no downtime', p: 'A payments network cannot schedule a cutover — every endpoint upgrades on its own timeline and the two schemes have to interoperate throughout. The handshake had to negotiate hybrid or classical per-connection with no visible change to either side.' },
      { k: 'Approach', h: 'Hybrid, measured, reversible', p: 'We combined X25519 with ML-KEM-768 in a single hybrid handshake, kept a classical-only fallback for endpoints that had not upgraded, and instrumented every negotiation. Cost was measured continuously rather than benchmarked once: mean overhead, tail latency, and CPU per handshake, published to the migration dashboard weekly.' },
      { k: 'Outcome', h: 'A migration nobody noticed', p: 'The overhead settled at 1.8ms mean added latency, comfortably inside the network SLA. Fallback to classical-only now happens on 0.03% of connections, almost entirely legacy terminals scheduled for replacement. No transaction was delayed or dropped during the rollout.' },
    ],
    timelineTitle: 'Migration phases',
    timeline: [
      { t: 'Q1', title: 'Hybrid handshake shipped', detail: 'X25519 + ML-KEM-768 negotiation added behind a feature flag.' },
      { t: 'Q2', title: 'Canary endpoints', detail: '40 low-traffic endpoints opted in; overhead measured continuously.' },
      { t: 'Q3', title: 'Fleet rollout begins', detail: 'Staged rollout across 4,900 endpoints at 5% per week.' },
      { t: 'Q4', title: 'Classical deprecation planned', detail: 'Remaining fallback traffic scheduled for terminal replacement.' },
    ],
  },
  {
    id: 'noise7', index: '04', title: 'Noise-7', tagline: 'An authenticated key exchange, formally checked', discipline: 'Protocol design', year: '2024',
    summary: 'A device fleet needed mutual authentication over an untrusted transport with no PKI and 32KB of flash to spare. We built a Noise-derived handshake and proved the properties we claimed with a symbolic model.',
    facts: [{ k: 'Client', v: 'Industrial sensor manufacturer' }, { k: 'Role', v: 'Protocol author' }, { k: 'Duration', v: 'Seven months' }, { k: 'Stack', v: 'Rust (no_std) · ProVerif' }],
    metrics: [{ label: 'Flash footprint', value: '21KB' }, { label: 'Handshake RTTs', value: '1.5' }, { label: 'Modelled properties', value: '9' }, { label: 'Findings, external audit', value: '2 low' }],
    body: [
      { k: 'Problem', h: 'No PKI, no room', p: 'The fleet ships without certificates and cannot be reached to rotate them. Devices hold a static keypair burned at manufacture and 32KB of flash for the entire security stack. TLS was never a candidate.' },
      { k: 'Approach', h: 'Prove it, then ship it', p: 'The handshake is a Noise XX variant with an added identity-hiding step for the responder. Before implementation we modelled it in ProVerif and checked nine properties: mutual authentication, forward secrecy, key compromise impersonation resistance, and identity confidentiality among them. Two properties failed on the first model and drove a change to the message ordering.' },
      { k: 'Outcome', h: '21 kilobytes', p: 'The shipped implementation is 21KB of flash including the primitives. An external audit returned two low-severity findings, both in the surrounding transport code rather than the handshake. The symbolic model is published alongside the implementation.' },
    ],
    timelineTitle: 'Design phases',
    timeline: [
      { t: 'M1', title: 'Requirements', detail: 'Identity hiding added as a hard requirement after a customer threat model review.' },
      { t: 'M2', title: 'First symbolic model', detail: 'Two of nine properties fail; message ordering revised.' },
      { t: 'M4', title: 'Reference implementation', detail: 'no_std Rust against the revised model, constant-time throughout.' },
      { t: 'M6', title: 'External audit', detail: 'Two low findings in transport framing; none in the handshake.' },
      { t: 'M7', title: 'Fleet rollout', detail: 'Shipped to 60,000 devices in a staged firmware update.' },
    ],
  },
  {
    id: 'redline', index: '05', title: 'Redline', tagline: 'Breaking a custodial wallet before someone else did', discipline: 'Audit & pentest', year: '2024',
    summary: "A four-week assessment of a custodial wallet's key management. We recovered a signing key from a memory dump, then again from a timing side channel, and wrote the remediation with the team that fixed it.",
    facts: [{ k: 'Client', v: 'Digital asset custodian' }, { k: 'Role', v: 'Lead assessor' }, { k: 'Duration', v: 'Four weeks' }, { k: 'Scope', v: 'Key management · signing service' }],
    metrics: [{ label: 'Findings', value: '14' }, { label: 'Critical', value: '2' }, { label: 'Keys recovered', value: '2 of 2' }, { label: 'Fix verified', value: '6 weeks' }],
    body: [
      { k: 'Scope', h: 'Assume the host is hostile', p: 'The engagement assumed an attacker with code execution on the signing host — the realistic case for a custodian with a large operations team. The question was not whether the perimeter held, but what an insider could take once inside it.' },
      { k: 'Findings', h: 'Two paths to the same key', p: 'The signing key was held in process memory unprotected for the lifetime of the service; a core dump yielded it in under a minute. Separately, the ECDSA nonce generation leaked through a variable-time scalar multiplication, recoverable in roughly 4,000 observed signatures via a lattice attack.' },
      { k: 'Remediation', h: 'Moved to hardware, made constant-time', p: 'Keys moved into an HSM with no export path, and the signing library was replaced with a constant-time implementation. We re-tested six weeks later: the memory path was gone and the timing channel was no longer measurable across two million signatures.' },
    ],
    timelineTitle: 'Assessment phases',
    timeline: [
      { t: 'W1', title: 'Threat model & scope', detail: 'Insider-with-execution agreed as the primary adversary.' },
      { t: 'W2', title: 'Memory disclosure', detail: 'Signing key recovered from an unprivileged core dump.' },
      { t: 'W3', title: 'Timing side channel', detail: 'Nonce bias recovered from ~4,000 signatures using a lattice attack.' },
      { t: 'W4', title: 'Report & walkthrough', detail: '14 findings delivered with reproduction harnesses for each.' },
      { t: 'W10', title: 'Retest', detail: 'Both critical findings closed and verified.' },
    ],
  },
  {
    id: 'cryptkit', index: '06', title: 'Cryptkit', tagline: 'A constant-time primitives library in Rust', discipline: 'Open source', year: '2023–',
    summary: 'An open-source library of constant-time primitives with a test suite that fails the build when timing variance exceeds threshold. Written because I kept auditing the same three mistakes.',
    facts: [{ k: 'Licence', v: 'Apache-2.0' }, { k: 'Role', v: 'Maintainer' }, { k: 'Started', v: '2023' }, { k: 'Stack', v: 'Rust · dudect · criterion' }],
    metrics: [{ label: 'Downloads / month', value: '41k' }, { label: 'Contributors', value: '23' }, { label: 'Primitives', value: '11' }, { label: 'Timing tests', value: '318' }],
    body: [
      { k: 'Motivation', h: 'The same three mistakes', p: 'Almost every timing finding I have written up came from one of three places: a comparison that returns early, a table lookup indexed by secret data, or a branch on a secret bit. The fixes are well known and rarely applied, because the failure is invisible until someone measures it.' },
      { k: 'Design', h: 'The test suite is the product', p: 'Each primitive ships with a dudect-style timing test that runs in CI and fails the build when the distribution of execution times correlates with input class. A correct implementation nobody can regress is worth more than a fast one.' },
      { k: 'Status', h: 'Eleven primitives, twenty-three contributors', p: 'The library covers comparison, encoding, field arithmetic and AEAD wrappers. It is not a replacement for a full cryptographic library and says so in the readme; it is the layer beneath one.' },
    ],
    timelineTitle: 'Project milestones',
    timeline: [
      { t: '2023', title: 'First release', detail: 'Constant-time comparison and hex encoding, 400 lines.' },
      { t: '2024', title: 'Timing CI', detail: 'dudect harness added; builds now fail on measurable variance.' },
      { t: '2025', title: 'External adoption', detail: 'Vendored by two wallet projects and one HSM firmware team.' },
      { t: '2026', title: 'Audit', detail: 'Community-funded review; no findings above informational.' },
    ],
  },
  {
    id: 'disclosure', index: '07', title: 'Disclosure Set 08', tagline: 'Eleven credited findings in TLS terminators', discipline: 'Research', year: '2023–2026',
    summary: 'Three years of coordinated disclosure against TLS-terminating proxies and load balancers. Eleven credited CVEs, all found by reading specifications against implementations rather than fuzzing.',
    facts: [{ k: 'Scope', v: 'TLS terminators · load balancers' }, { k: 'Role', v: 'Researcher' }, { k: 'Disclosure', v: '90-day coordinated' }, { k: 'Credited', v: '11 CVEs' }],
    metrics: [{ label: 'Credited CVEs', value: '11' }, { label: 'Highest CVSS', value: '8.6' }, { label: 'Vendors', value: '6' }, { label: 'Median fix time', value: '47d' }],
    body: [
      { k: 'Method', h: 'Read the specification, then the code', p: 'Fuzzing finds memory safety bugs. It rarely finds a state machine that accepts a message it should reject. Every finding in this set came from reading an RFC beside an implementation and asking what happens when a peer does the thing the specification forbids.' },
      { k: 'Pattern', h: 'Session resumption is where it hides', p: 'Six of the eleven findings sit in session resumption or renegotiation paths — code that runs rarely, is tested less, and carries authentication state across connections. Two allowed a client\'s authenticated session to be inherited by an unauthenticated one.' },
      { k: 'Process', h: 'Ninety days, published either way', p: 'All disclosures follow a 90-day coordinated timeline with an extension where a vendor is visibly working. Advisories publish on day 90 regardless. Median fix time across six vendors was 47 days.' },
    ],
    timelineTitle: 'Disclosure cadence',
    timeline: [
      { t: '2023', title: 'First three', detail: 'Two resumption state-machine flaws and one certificate-chain validation bypass.' },
      { t: '2024', title: 'Vendor programme', detail: 'Ongoing review arrangement with two vendors ahead of release.' },
      { t: '2025', title: 'Highest severity', detail: 'CVSS 8.6 session inheritance flaw; fixed in 19 days.' },
      { t: '2026', title: 'Set closed', detail: 'Eleventh advisory published. Findings summarised in a write-up.' },
    ],
  },
];

export const STATS = [
  { label: 'Years in security', value: '06', note: 'Four of them writing production cryptography' },
  { label: 'Credited CVEs', value: '11', note: 'Coordinated disclosure across six vendors' },
  { label: 'Incidents led', value: '23', note: 'From first alert through post-mortem' },
  { label: 'Detections shipped', value: '912', note: 'Version-controlled, tested, owned' },
];

export const CAPABILITIES = [
  { index: '01', title: 'Detection engineering', body: 'Sigma rulesets under version control, gated by telemetry-backed tests. Coverage measured against ATT&CK, not rule count.' },
  { index: '02', title: 'Incident response', body: 'Lead or embedded. Containment first, attribution last, and a written post-mortem that names the control that failed.' },
  { index: '03', title: 'Protocol implementation', body: 'Authenticated key exchange, AEAD constructions, and the constant-time discipline that keeps them honest.' },
  { index: '04', title: 'Post-quantum migration', body: 'Hybrid handshakes, inventory of long-lived secrets, and a measured cost per endpoint before anything ships.' },
  { index: '05', title: 'Security audit', body: 'Specification against implementation. Reproduction harnesses for every finding, and a retest when it is fixed.' },
  { index: '06', title: 'Tooling', body: 'Open-source primitives and the timing harnesses that prove they are constant-time in CI, not in principle.' },
];

export const BIO_FACTS = [
  { k: 'Now', v: 'Independent · detection and cryptography' },
  { k: 'Before', v: 'Tier-2 SOC, then detection lead' },
  { k: 'Languages', v: 'Rust, Python, Go (reading)' },
  { k: 'Writes', v: 'Sigma, ProVerif models, post-mortems' },
  { k: 'Availability', v: 'Q4 2026 onward' },
];

export const RECOGNITION = [
  { index: '01', title: 'Certifications', items: [
    { name: 'Offensive Security Certified Professional', detail: 'OSCP', meta: '2021' },
    { name: 'GIAC Exploit Researcher and Advanced Penetration Tester', detail: 'GXPN', meta: '2023' },
    { name: 'Certified Information Systems Security Professional', detail: 'CISSP', meta: '2024' },
    { name: 'Certified Kubernetes Security Specialist', detail: 'CKS', meta: '2025' },
  ] },
  { index: '02', title: 'CVE credits', items: [
    { name: 'Session inheritance across resumption in a TLS terminator', detail: 'CVE-2025-41802', meta: 'CVSS 8.6' },
    { name: 'Certificate chain validation bypass on renegotiation', detail: 'CVE-2024-33917', meta: 'CVSS 7.4' },
    { name: 'Nonce reuse in an AEAD wrapper under key rotation', detail: 'CVE-2024-28450', meta: 'CVSS 6.8' },
    { name: 'Timing disclosure in ECDSA scalar multiplication', detail: 'CVE-2023-51166', meta: 'CVSS 5.9' },
  ] },
  { index: '03', title: 'Talks', items: [
    { name: 'Detections are a codebase, not a console', detail: 'Nullcon Goa', meta: '2026' },
    { name: 'Nine hours: anatomy of a signing key compromise', detail: 'BSides Bangalore', meta: '2025' },
    { name: 'Shipping hybrid post-quantum without a flag day', detail: 'Real World Crypto', meta: '2026' },
  ] },
  { index: '04', title: 'Publications', items: [
    { name: 'A symbolic model for identity-hiding Noise variants', detail: 'IACR ePrint', meta: '2024' },
    { name: 'Measuring the cost of ML-KEM in payments', detail: 'Whitepaper', meta: '2026' },
  ] },
  { index: '05', title: 'Bug bounty', items: [
    { name: 'Cumulative payouts across four programmes', detail: '$184,000', meta: '2021–2026' },
    { name: 'Accepted submissions', detail: '63 of 91', meta: '69%' },
    { name: 'Highest single award', detail: '$45,000', meta: '2025' },
  ] },
  { index: '06', title: 'Worked with', items: [
    { name: 'Tier-1 payments processor · European logistics operator', detail: 'Retained', meta: '2024–26' },
    { name: 'Regional card network · Digital asset custodian', detail: 'Project', meta: '2024–26' },
    { name: 'Industrial sensor manufacturer', detail: 'Project', meta: '2024' },
  ] },
];

export const CONTACT_ROWS = [
  { k: 'Email', v: 'tanisha@brahma.sec', href: 'mailto:tanisha@brahma.sec' },
  { k: 'Signal', v: '@tanisha.42', href: '#contact' },
  { k: 'GitHub', v: 'github.com/tbrahma', href: '#contact' },
  { k: 'Mastodon', v: '@tanisha@infosec.exchange', href: '#contact' },
  { k: 'Speaking', v: 'Request a topic list', href: 'mailto:tanisha@brahma.sec' },
];

export const PGP_FINGERPRINT = '9F2C 47AD 10B8 6E31 D4A9  2C05 88FE 7B14 A3D6 0E92';
