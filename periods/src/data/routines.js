// DRAFT: needs owner review before launch
//
// All checklist content lives here. To edit:
// - Change any `title` (keep it short, 8 words or fewer) or `why` (one line, optional).
// - Add an item by copying a line; give it a new unique `id` (never reuse or rename an id
//   someone may already have ticked, or their old ticks will stop matching).
// - `category` must be one of the CATEGORIES keys below.

export const CATEGORIES = {
  body: 'Body',
  nourish: 'Nourish',
  move: 'Move',
  mind: 'Mind',
  rest: 'Rest',
};

export const ROUTINES = {
  menstrual: {
    name: 'Menstrual',
    season: 'Inner winter',
    description: 'Your period. Energy often runs lower, so slow down and keep things soft.',
    items: [
      { id: 'm-warmth', category: 'body', title: 'Rest a heat pack on your belly', why: 'Warmth can feel soothing.' },
      { id: 'm-comfy', category: 'body', title: 'Wear something soft and comfortable' },
      { id: 'm-warm-drink', category: 'nourish', title: 'Sip a warm drink you enjoy' },
      { id: 'm-water', category: 'nourish', title: 'Keep a water bottle within reach' },
      {
        id: 'm-iron-meal',
        category: 'nourish',
        title: 'Eat a warm, filling meal',
        why: 'Soups, stews and greens are easy comfort.',
      },
      { id: 'm-stretch', category: 'move', title: 'Five minutes of gentle stretching' },
      { id: 'm-walk', category: 'move', title: 'Take a slow, short walk', why: 'Only if you feel like it.' },
      { id: 'm-journal', category: 'mind', title: 'Write down how you feel today' },
      { id: 'm-no', category: 'mind', title: 'Say no to one thing' },
      { id: 'm-early', category: 'rest', title: 'Head to bed a little earlier' },
      { id: 'm-pause', category: 'rest', title: 'Take one guilt-free pause' },
    ],
  },
  follicular: {
    name: 'Follicular',
    season: 'Inner spring',
    description: 'After your period. Many people feel fresh energy and curiosity here.',
    items: [
      { id: 'f-sunlight', category: 'body', title: 'Get some morning daylight' },
      { id: 'f-skin', category: 'body', title: 'Give your skin a little care' },
      {
        id: 'f-fresh',
        category: 'nourish',
        title: 'Add something fresh and colourful',
        why: 'Crunchy veg, fruit, sprouts.',
      },
      { id: 'f-new-recipe', category: 'nourish', title: 'Try a new recipe' },
      { id: 'f-water', category: 'nourish', title: 'Drink water through the day' },
      { id: 'f-new-move', category: 'move', title: 'Try a new workout or class' },
      { id: 'f-dance', category: 'move', title: 'Dance to one favourite song' },
      { id: 'f-plan', category: 'mind', title: 'Plan one thing you look forward to' },
      { id: 'f-learn', category: 'mind', title: 'Learn something small and new' },
      { id: 'f-wind-down', category: 'rest', title: 'Screen-free last half hour' },
    ],
  },
  ovulatory: {
    name: 'Ovulatory',
    season: 'Inner summer',
    description: 'Mid-cycle. Often a social, confident, outward-facing stretch.',
    items: [
      { id: 'o-water', category: 'body', title: 'Stay well hydrated today' },
      { id: 'o-dress-up', category: 'body', title: 'Wear something that feels like you' },
      { id: 'o-light-meal', category: 'nourish', title: 'Enjoy a light, bright meal', why: 'Salads, grains, fruit.' },
      { id: 'o-fibre', category: 'nourish', title: 'Add fibre: greens, beans or seeds' },
      { id: 'o-active', category: 'move', title: 'Do a workout you love' },
      { id: 'o-outside', category: 'move', title: 'Move outside for a while' },
      { id: 'o-connect', category: 'mind', title: 'Call or meet someone you like' },
      { id: 'o-speak-up', category: 'mind', title: 'Share an idea you have been holding' },
      { id: 'o-gratitude', category: 'mind', title: 'Note three things going well' },
      { id: 'o-recharge', category: 'rest', title: 'Leave a quiet gap in your day' },
    ],
  },
  luteal: {
    name: 'Luteal',
    season: 'Inner autumn',
    description: 'Before your period. Turn inward, finish things, and be extra kind to yourself.',
    items: [
      { id: 'l-bath', category: 'body', title: 'Have a warm shower or bath' },
      { id: 'l-massage', category: 'body', title: 'Massage your shoulders or feet' },
      {
        id: 'l-regular-meals',
        category: 'nourish',
        title: 'Eat regular, steady meals',
        why: 'Can help keep energy even.',
      },
      { id: 'l-cravings', category: 'nourish', title: 'Enjoy a treat without guilt' },
      { id: 'l-less-caffeine', category: 'nourish', title: 'Swap one coffee for herbal tea' },
      { id: 'l-yoga', category: 'move', title: 'Gentle yoga or pilates' },
      { id: 'l-walk', category: 'move', title: 'Take an easy walk' },
      { id: 'l-tidy', category: 'mind', title: 'Finish or tidy one small task' },
      { id: 'l-brain-dump', category: 'mind', title: 'Brain-dump worries onto paper' },
      { id: 'l-boundaries', category: 'mind', title: 'Protect some time for yourself' },
      { id: 'l-sleep', category: 'rest', title: 'Keep a calm bedtime routine' },
    ],
  },
};

export const LATE_NOTE = 'Your period may be late. Log it when it starts. Until then, here is your luteal list.';

export const DISCLAIMER =
  'General wellness ideas, not medical advice. Talk to a doctor about pain, very heavy bleeding, or big changes in your cycle.';
