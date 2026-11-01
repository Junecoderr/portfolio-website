import { COLOR_KEYS, DEFAULT_PALETTE, paletteById } from '../data/palettes.js';

export const FONTS = [
  { id: 'serif-sans', label: 'Instrument Serif + Geist', heading: "'Instrument Serif', Georgia, serif" },
  { id: 'sans', label: 'Geist', heading: "'Geist', system-ui, sans-serif" },
  { id: 'mono-sans', label: 'Geist Mono + Geist', heading: "'Geist Mono', ui-monospace, monospace" },
  { id: 'system', label: 'System', heading: 'Georgia, Cambria, serif', body: 'system-ui, -apple-system, sans-serif' },
];
const BODY_DEFAULT = "'Geist', system-ui, -apple-system, sans-serif";

export const DEFAULT_APPEARANCE = {
  palette: DEFAULT_PALETTE,
  custom: null, // { light: {...colors}, dark: {...colors} } once the person edits colours
  mode: 'system',
  font: 'serif-sans',
  density: 'roomy',
};

export const MIN_CONTRAST = 4.5;
const HEX_RE = /^#[0-9a-f]{6}$/i;

export const isHex = (v) => typeof v === 'string' && HEX_RE.test(v);

function luminance(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2 contrast ratio between two #rrggbb colours. */
export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The pairs that carry body text; all must reach 4.5:1. */
export function checkContrast(colors) {
  const pairs = [
    { label: 'Text on background', ratio: contrast(colors.text, colors.bg) },
    { label: 'Text on cards', ratio: contrast(colors.text, colors.surface) },
  ];
  return { ok: pairs.every((p) => p.ratio >= MIN_CONTRAST), pairs };
}

/** Black or white, whichever reads better on the given fill. */
export function onColor(hex) {
  return contrast(hex, '#111111') >= contrast(hex, '#ffffff') ? '#111111' : '#ffffff';
}

export function resolveMode(mode, systemDark) {
  return mode === 'system' ? (systemDark ? 'dark' : 'light') : mode;
}

export function resolveColors(appearance, mode) {
  return appearance.custom?.[mode] ?? paletteById(appearance.palette)[mode];
}

/** Writes the theme to :root as custom properties and data attributes. */
export function applyTheme(appearance, systemDark) {
  const root = document.documentElement;
  const mode = resolveMode(appearance.mode, systemDark);
  const colors = resolveColors(appearance, mode);
  for (const key of COLOR_KEYS) root.style.setProperty(`--${key}`, colors[key]);
  root.style.setProperty('--on-accent', onColor(colors.accent));
  const font = FONTS.find((f) => f.id === appearance.font) ?? FONTS[0];
  root.style.setProperty('--font-heading', font.heading);
  root.style.setProperty('--font-body', font.body ?? BODY_DEFAULT);
  root.dataset.mode = mode;
  root.dataset.density = appearance.density;
  root.style.colorScheme = mode;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', colors.bg);
}

export function isValidColors(c) {
  return !!c && COLOR_KEYS.every((k) => isHex(c[k]));
}
