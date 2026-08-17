# Graph Report - .  (2026-08-13)

## Corpus Check
- Corpus is ~2,627 words - fits in a single context window. You may not need a graph.

## Summary
- 64 nodes · 83 edges · 8 communities
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.95)
- Token cost: 72,836 input · 0 output

## Community Hubs (Navigation)
- App Shell & Scroll Behavior
- React + npm Package Config
- Header/Footer & Content Sections
- Vite HTML Entry Point
- Vite Build Tooling
- WhyMe Section & Pointer Effect
- Quote Section & Scan Grid Effect

## God Nodes (most connected - your core abstractions)
1. `scripts` - 4 edges
2. `App()` - 4 edges
3. `onNav()` - 4 edges
4. `index.html (Vite entry point)` - 4 edges
5. `useScrollReveal()` - 3 edges
6. `useScrollSpy()` - 3 edges
7. `react` - 2 edges
8. `react-dom` - 2 edges
9. `@vitejs/plugin-react` - 2 edges
10. `vite` - 2 edges

## Surprising Connections (you probably didn't know these)
- `App()` --calls--> `useScrollReveal()`  [EXTRACTED]
  src/App.jsx → src/hooks/useScrollReveal.js
- `App()` --calls--> `useScrollSpy()`  [EXTRACTED]
  src/App.jsx → src/hooks/useScrollSpy.js

## Import Cycles
- None detected.

## Communities (8 total, 0 thin omitted)

### Community 0 - "App Shell & Scroll Behavior"
Cohesion: 0.19
Nodes (9): App(), ParticleField3D(), useScrollReveal(), SECTION_IDS, useScrollSpy(), Process(), STEPS, Tooling() (+1 more)

### Community 1 - "React + npm Package Config"
Cohesion: 0.14
Nodes (13): dependencies, react, react-dom, name, private, scripts, build, dev (+5 more)

### Community 2 - "Header/Footer & Content Sections"
Cohesion: 0.21
Nodes (8): EXPLORE_LINKS, Footer(), Header(), NAV_LINKS, onNav(), Overview(), POSTS, Writing()

### Community 3 - "Vite HTML Entry Point"
Cohesion: 0.40
Nodes (6): index.html (Vite entry point), module script tag loading /src/main.jsx, Meta description: security operations, detection engineering, applied cryptography, Page title: Tanisha Brahma — SOC · Cryptography, #root mount element, src/main.jsx (React root entry)

### Community 4 - "Vite Build Tooling"
Cohesion: 0.40
Nodes (5): devDependencies, vite, @vitejs/plugin-react, vite, @vitejs/plugin-react

### Community 5 - "WhyMe Section & Pointer Effect"
Cohesion: 0.50
Nodes (3): PointerBloom(), REASONS, WhyMe()

### Community 6 - "Quote Section & Scan Grid Effect"
Cohesion: 0.50
Nodes (3): ACCENTS, ScanGrid(), Quote()

## Knowledge Gaps
- **21 isolated node(s):** `name`, `private`, `version`, `type`, `dev` (+16 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `devDependencies` connect `Vite Build Tooling` to `React + npm Package Config`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **What connects `name`, `private`, `version` to the rest of the system?**
  _21 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `React + npm Package Config` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._