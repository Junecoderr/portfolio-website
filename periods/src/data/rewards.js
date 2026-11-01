// Milestones and what they unlock. Edit titles and notes freely; keep the ids stable, since unlocks are
// saved by id on people's devices.
//
// kind 'streak': reached when the longest gentle streak hits `value` days.
// kind 'cycles': reached when `value` full cycles are logged (period start to next period start).

export const MILESTONES = [
  {
    id: 'streak-7',
    kind: 'streak',
    value: 7,
    title: 'One gentle week',
    hint: 'Care for yourself on 7 days',
    badge: 'sprout',
    palette: 'clay',
    note: 'Seven days of small kindnesses. That is how habits grow: quietly, one day at a time.',
  },
  {
    id: 'streak-14',
    kind: 'streak',
    value: 14,
    title: 'Two weeks of tending',
    hint: 'Care for yourself on 14 days',
    badge: 'leaf',
    palette: 'tide',
    note: 'Two weeks in. Notice which items you reach for most; they are telling you what you need.',
  },
  {
    id: 'streak-28',
    kind: 'streak',
    value: 28,
    title: 'A full moon of care',
    hint: 'Care for yourself on 28 days',
    badge: 'bloom',
    palette: 'night-bloom',
    note: 'Twenty-eight days, about one whole cycle of looking after yourself. That is something to be proud of.',
  },
  {
    id: 'cycle-1',
    kind: 'cycles',
    value: 1,
    title: 'First full cycle',
    hint: 'Log your next period start',
    badge: 'moon',
    note: 'You have tracked one full cycle. Tip: in your luteal phase, plan a lighter week ahead if you can. Future you will thank you.',
  },
  {
    id: 'cycles-3',
    kind: 'cycles',
    value: 3,
    title: 'Three cycles known',
    hint: 'Log 3 full cycles',
    badge: 'circle',
    insights: true,
    note: 'Three cycles logged. You can now see your own patterns in Insights below.',
  },
];

export function milestoneById(id) {
  return MILESTONES.find((m) => m.id === id);
}
