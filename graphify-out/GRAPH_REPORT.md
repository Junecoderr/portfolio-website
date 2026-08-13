# Graph Report - .  (2026-08-13)

## Corpus Check
- Corpus is ~957 words - fits in a single context window. You may not need a graph.

## Summary
- 24 nodes · 31 edges · 4 communities
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 4 edges (avg confidence: 0.88)
- Token cost: 0 input · 90,621 output

## Community Hubs (Navigation)
- Page Structure & Content
- Navigation & Identity
- Vanilla JS Behaviors
- FAQ & Scroll Interactions

## God Nodes (most connected - your core abstractions)
1. `script.js` - 4 edges
2. `Site Navigation Header` - 4 edges
3. `Placeholder Content Convention` - 4 edges
4. `Services Section` - 3 edges
5. `FAQ Section` - 3 edges
6. `Site Footer` - 3 edges
7. `Hamburger Menu Toggle` - 2 edges
8. `Why Work With Me Section` - 2 edges
9. `Process Section` - 2 edges
10. `Portfolio / Projects Section` - 2 edges

## Surprising Connections (you probably didn't know these)
- `Hamburger Menu Toggle` --references--> `script.js`  [INFERRED]
  index.html → index.html  _Bridges community 3 → community 1_
- `FAQ Section` --implements--> `Placeholder Content Convention`  [EXTRACTED]
  index.html → index.html  _Bridges community 3 → community 0_

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Single-Page Anchor Navigation Flow** — index_nav_header, index_hero_section, index_about_section, index_services_section, index_process_section, index_portfolio_section, index_faq_section, index_contact_section, index_footer [EXTRACTED 0.90]
- **script.js Vanilla-JS Behaviors** — index_script_js, index_hamburger_toggle, index_faq_accordion, index_reveal_animation [INFERRED 0.85]

## Communities (4 total, 0 thin omitted)

### Community 0 - "Page Structure & Content"
Cohesion: 0.27
Nodes (9): Why Work With Me Section, Contact / CTA Section, Google Fonts (Inter), Hero Section, Placeholder Content Convention, Portfolio / Projects Section, Process Section, Services Section (+1 more)

### Community 1 - "Navigation & Identity"
Cohesion: 0.50
Nodes (5): Anchor-Linked Single-Page Nav, Site Footer, Hamburger Menu Toggle, Site Navigation Header, Tanisha Brahma

### Community 2 - "Vanilla JS Behaviors"
Cohesion: 0.40
Nodes (4): hamburger, navLinks, revealElements, revealObserver

### Community 3 - "FAQ & Scroll Interactions"
Cohesion: 0.50
Nodes (4): FAQ Accordion Pattern, FAQ Section, Scroll-Reveal Animation Hook, script.js

## Knowledge Gaps
- **8 isolated node(s):** `hamburger`, `navLinks`, `revealElements`, `revealObserver`, `styles.css` (+3 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Site Navigation Header` connect `Navigation & Identity` to `Page Structure & Content`?**
  _High betweenness centrality (0.091) - this node is a cross-community bridge._
- **Why does `script.js` connect `FAQ & Scroll Interactions` to `Page Structure & Content`, `Navigation & Identity`?**
  _High betweenness centrality (0.061) - this node is a cross-community bridge._
- **Why does `Site Footer` connect `Navigation & Identity` to `Page Structure & Content`?**
  _High betweenness centrality (0.055) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `script.js` (e.g. with `FAQ Accordion Pattern` and `Hamburger Menu Toggle`) actually correct?**
  _`script.js` has 3 INFERRED edges - model-reasoned connections that need verification._
- **What connects `hamburger`, `navLinks`, `revealElements` to the rest of the system?**
  _8 weakly-connected nodes found - possible documentation gaps or missing edges._