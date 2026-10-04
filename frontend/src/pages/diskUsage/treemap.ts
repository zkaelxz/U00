// Squarified treemap layout (Bruls, Huizing, van Wijk) for the Disk usage view.
// Pure: rectangles in the same units as the width and height given.

export type TreemapInput = { id: string; value: number }
export type TreemapRect = { id: string; x: number; y: number; w: number; h: number }

type Cell = { id: string; area: number }

const sum = (cells: Cell[]) => cells.reduce((s, c) => s + c.area, 0)

// The worst aspect ratio in a row laid along a side of this length.
function worst(row: Cell[], side: number): number {
  const total = sum(row)
  const areas = row.map((c) => c.area)
  const max = Math.max(...areas)
  const min = Math.min(...areas)
  return Math.max((side * side * max) / (total * total), (total * total) / (side * side * min))
}

/** Items of value 0 (or less) get no rectangle. Rectangles tile width x height exactly, biggest first. */
export function squarify(items: TreemapInput[], width: number, height: number): TreemapRect[] {
  const positive = items.filter((i) => i.value > 0).sort((a, b) => b.value - a.value)
  if (!positive.length || width <= 0 || height <= 0) return []
  const scale = (width * height) / positive.reduce((s, i) => s + i.value, 0)
  const out: TreemapRect[] = []
  let x = 0
  let y = 0
  let w = width
  let h = height
  let row: Cell[] = []

  const flush = () => {
    if (!row.length) return
    const total = sum(row)
    if (w >= h) {
      const colW = total / h
      let cy = y
      for (const c of row) {
        const ch = c.area / colW
        out.push({ id: c.id, x, y: cy, w: colW, h: ch })
        cy += ch
      }
      x += colW
      w -= colW
    } else {
      const rowH = total / w
      let cx = x
      for (const c of row) {
        const cw = c.area / rowH
        out.push({ id: c.id, x: cx, y, w: cw, h: rowH })
        cx += cw
      }
      y += rowH
      h -= rowH
    }
    row = []
  }

  for (const item of positive) {
    const cell = { id: item.id, area: item.value * scale }
    const side = Math.min(w, h)
    if (!row.length || worst([...row, cell], side) <= worst(row, side)) row.push(cell)
    else {
      flush()
      row = [cell]
    }
  }
  flush()
  return out
}
