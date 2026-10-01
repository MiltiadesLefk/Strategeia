// Squarified treemap (Bruls, Huizing, van Wijk): lays weighted items out as
// rectangles with aspect ratios as close to square as possible, so small
// tiles stay readable. Written for the Market Terminal heatmap, which only
// needs positions (no d3 dependency). Positions are in the unit square, so
// the caller can place them with percentages and never needs a resize hook.

export interface TreemapRect<T> {
  item: T;
  x: number;
  y: number;
  w: number;
  h: number;
}

function worst(row: number[], side: number): number {
  const sum = row.reduce((a, b) => a + b, 0);
  const max = Math.max(...row);
  const min = Math.min(...row);
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
}

/** Items with a weight of zero or less have no area and are dropped. */
export function squarify<T>(items: T[], weight: (item: T) => number, box = { x: 0, y: 0, w: 1, h: 1 }): TreemapRect<T>[] {
  const sorted = items.filter((i) => weight(i) > 0).sort((a, b) => weight(b) - weight(a));
  const total = sorted.reduce((a, i) => a + weight(i), 0);
  if (sorted.length === 0 || total <= 0 || box.w <= 0 || box.h <= 0) return [];

  // Convert weights to areas inside the box.
  const area = box.w * box.h;
  const areas = sorted.map((i) => (weight(i) / total) * area);
  const out: TreemapRect<T>[] = [];
  let { x, y, w, h } = box;
  let start = 0;

  while (start < sorted.length) {
    const side = Math.min(w, h);
    let end = start + 1;
    let row = areas.slice(start, end);
    while (end < sorted.length) {
      const next = areas.slice(start, end + 1);
      if (worst(next, side) <= worst(row, side)) {
        row = next;
        end += 1;
      } else break;
    }
    const rowSum = row.reduce((a, b) => a + b, 0);
    if (w >= h) {
      // Lay the row as a column on the left.
      const colW = rowSum / h;
      let cy = y;
      row.forEach((a, idx) => {
        const rh = a / colW;
        out.push({ item: sorted[start + idx], x, y: cy, w: colW, h: rh });
        cy += rh;
      });
      x += colW;
      w -= colW;
    } else {
      const rowH = rowSum / w;
      let cx = x;
      row.forEach((a, idx) => {
        const rw = a / rowH;
        out.push({ item: sorted[start + idx], x: cx, y, w: rw, h: rowH });
        cx += rw;
      });
      y += rowH;
      h -= rowH;
    }
    start = end;
  }
  return out;
}
