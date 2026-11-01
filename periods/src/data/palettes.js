// Recommended colour palettes. `unlock` names the milestone in rewards.js that makes a palette selectable;
// null means it is available from the start. Every palette needs both a light and a dark set.

export const COLOR_KEYS = ['bg', 'surface', 'text', 'accent', 'menstrual', 'follicular', 'ovulatory', 'luteal'];

export const COLOR_LABELS = {
  bg: 'Background',
  surface: 'Cards',
  text: 'Text',
  accent: 'Accent',
  menstrual: 'Menstrual phase',
  follicular: 'Follicular phase',
  ovulatory: 'Ovulatory phase',
  luteal: 'Luteal phase',
};

export const PALETTES = [
  {
    id: 'blush',
    name: 'Blush',
    unlock: null,
    light: {
      bg: '#fbf7f4',
      surface: '#ffffff',
      text: '#2b2024',
      accent: '#a8455a',
      menstrual: '#c2566b',
      follicular: '#7fa88b',
      ovulatory: '#e0a84f',
      luteal: '#8c7bb8',
    },
    dark: {
      bg: '#1a1416',
      surface: '#251d20',
      text: '#f3e9ec',
      accent: '#e8909f',
      menstrual: '#e07a8e',
      follicular: '#93c2a1',
      ovulatory: '#f0c06e',
      luteal: '#a99ad6',
    },
  },
  {
    id: 'sage',
    name: 'Sage',
    unlock: null,
    light: {
      bg: '#f4f6f1',
      surface: '#ffffff',
      text: '#1f2a22',
      accent: '#3f6b4b',
      menstrual: '#b8606a',
      follicular: '#6f9e78',
      ovulatory: '#d4a64a',
      luteal: '#7f8fb0',
    },
    dark: {
      bg: '#131814',
      surface: '#1c231d',
      text: '#e7efe8',
      accent: '#8fc29c',
      menstrual: '#d98590',
      follicular: '#8fc29c',
      ovulatory: '#e8c372',
      luteal: '#9fb0d4',
    },
  },
  {
    id: 'lavender',
    name: 'Lavender',
    unlock: null,
    light: {
      bg: '#f7f5fb',
      surface: '#ffffff',
      text: '#25213a',
      accent: '#5d4a9c',
      menstrual: '#c0587a',
      follicular: '#76a9a0',
      ovulatory: '#dba95a',
      luteal: '#8a78c4',
    },
    dark: {
      bg: '#16141f',
      surface: '#201d2c',
      text: '#ece8f7',
      accent: '#b3a3ec',
      menstrual: '#e283a2',
      follicular: '#8fc7bd',
      ovulatory: '#ecc27d',
      luteal: '#b3a3ec',
    },
  },
  {
    id: 'clay',
    name: 'Clay',
    unlock: 'streak-7',
    light: {
      bg: '#faf5ef',
      surface: '#fffdf9',
      text: '#2e221a',
      accent: '#9a4a2c',
      menstrual: '#b5523a',
      follicular: '#8aa167',
      ovulatory: '#d9a03f',
      luteal: '#8a6f9e',
    },
    dark: {
      bg: '#1b1510',
      surface: '#261e17',
      text: '#f4ebe1',
      accent: '#e49a76',
      menstrual: '#e08467',
      follicular: '#adc287',
      ovulatory: '#ecbd66',
      luteal: '#b59ac7',
    },
  },
  {
    id: 'tide',
    name: 'Tide',
    unlock: 'streak-14',
    light: {
      bg: '#f2f7f8',
      surface: '#ffffff',
      text: '#14262b',
      accent: '#1f6575',
      menstrual: '#c25b6e',
      follicular: '#4f9d8f',
      ovulatory: '#e3ad55',
      luteal: '#6a7fc0',
    },
    dark: {
      bg: '#0f181b',
      surface: '#172327',
      text: '#e3f0f2',
      accent: '#7cc6d6',
      menstrual: '#e48799',
      follicular: '#79c4b6',
      ovulatory: '#f0c47c',
      luteal: '#99a9e0',
    },
  },
  {
    id: 'night-bloom',
    name: 'Night bloom',
    unlock: 'streak-28',
    light: {
      bg: '#fdf6f8',
      surface: '#ffffff',
      text: '#1d1420',
      accent: '#7a2a5c',
      menstrual: '#b0305a',
      follicular: '#3f8f74',
      ovulatory: '#d9922b',
      luteal: '#6b4fae',
    },
    dark: {
      bg: '#120c14',
      surface: '#1e1521',
      text: '#f7eaf2',
      accent: '#f08cc4',
      menstrual: '#f07aa0',
      follicular: '#6fd0ac',
      ovulatory: '#f5b65c',
      luteal: '#b394f5',
    },
  },
];

export const DEFAULT_PALETTE = 'blush';

export function paletteById(id) {
  return PALETTES.find((p) => p.id === id) ?? PALETTES[0];
}
