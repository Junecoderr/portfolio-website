# Cycle care

A daily self-care checklist for each phase of the menstrual cycle. Live at https://periods.tanisha.ceo.

Private by design: everything is stored in the browser's `localStorage` on the person's own device. There is no
backend, no account, no analytics and no third-party script.

## Stack

- Vite 5 + React 18, no router. Views switch on the URL hash: `#/today`, `#/day/YYYY-MM-DD`, `#/calendar`,
  `#/collection`, `#/settings`.
- Plain CSS with custom properties; the theme is written to `:root` at runtime by `src/lib/theme.js`.
- Self-hosted fonts in `public/fonts/` (Geist, Geist Mono, Instrument Serif; copied from the portfolio).

## Run

```bash
cd periods
npm install
npm run dev        # local dev server
npm run build      # production build into dist/
npm run preview    # serve dist/
npm test           # node:test unit tests (cycle math, streaks, palettes, import validation)
```

## Where things live

| Path | Purpose |
| --- | --- |
| `src/data/routines.js` | **All checklist content**: phase names, descriptions and items. Edit freely; never rename an item `id` |
| `src/data/rewards.js` | Milestones (7/14/28-day streaks, 1 and 3 logged cycles), badges, notes, what each unlocks |
| `src/data/palettes.js` | The 6 colour palettes (light + dark) and which milestone unlocks each |
| `src/lib/cycle.js` | Pure date and phase math on local `YYYY-MM-DD` keys |
| `src/lib/streaks.js` | Gentle streaks, milestone checks, insights |
| `src/lib/store.js` | `localStorage` load/save under `periods:v1`, backup validation |
| `src/lib/theme.js` | Palettes to CSS variables, fonts, WCAG contrast check |
| `src/views/*` | Onboarding, Today/Day, Calendar, Collection, Settings |
| `tests/*` | Unit tests (`node --test`) |

## How the cycle is worked out

- Cycle day 1 is the logged period start. Menstrual: day ≤ period length. Ovulatory: estimated ovulation day
  (cycle length − 14) ± 1. Follicular is between; luteal is after.
- Cycle length is the average of the last 6 logged cycles between 21 and 45 days, or the setup value until 2 are
  logged. Past this length the period shows as possibly late and the luteal list stays.
- Streaks: a day counts when one item is ticked; up to 2 missed days per cycle do not break a streak.

## Deploy (Vercel)

Its own Vercel project, like `code/`:

1. New project from this repo, **Root Directory** `periods`. Framework preset Vite is detected; no env vars.
2. Add the domain `periods.tanisha.ceo` and the DNS record Vercel shows.

`vercel.json` sets cache headers and a strict CSP (`'self'` only). Adding any third-party script or host needs a CSP
change, and should be avoided: this app holds health data.

## Content

The checklist text is general wellness guidance, not medical advice; the footer says so on every page. Review
`src/data/routines.js` before launch (it is marked DRAFT).
