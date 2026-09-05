/**
 * Resolves top 9 books: MRU first, fills remaining slots with A-Z.
 * @param {Array} items - Library books from /api/library
 * @returns {Array} Top 9 items
 */
export function getTop9Recent(items = []) {
  if (!Array.isArray(items) || items.length === 0) return [];

  const accessed = [];
  const unaccessed = [];

  for (const item of items) {
    if (item.lastAccessed && item.lastAccessed > 0) {
      accessed.push(item);
    } else {
      unaccessed.push(item);
    }
  }

  accessed.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
  unaccessed.sort((a, b) =>
    (a.fileName || "").localeCompare(b.fileName || "", undefined, { sensitivity: "base" })
  );

  return [...accessed, ...unaccessed].slice(0, 9);
}
