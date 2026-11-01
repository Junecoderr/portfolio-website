// Display helpers. Keys become local Date objects only for formatting, never for date math.
export function keyToLocalDate(key) {
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d);
}

export function formatDate(key, options = { weekday: 'long', day: 'numeric', month: 'long' }) {
  return keyToLocalDate(key).toLocaleDateString(undefined, options);
}
