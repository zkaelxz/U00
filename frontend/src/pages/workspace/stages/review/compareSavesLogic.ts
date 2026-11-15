import { lineNumber } from '../../../../lineNumber'

export interface SaveLine {
  id: number | null
  idx: number | null
  zh: string
  en: string
}

export interface SaveRow {
  // The line's permanent id; both saves are matched on it, never on position.
  id: number
  number: number
  zh: string
  left: string | null
  right: string | null
}

export type WordPart = { text: string; changed: boolean }

// Lines present in only one save (merged, split or added between the saves) are
// kept with null on the missing side instead of being force-matched by position.
export function compareSaves(left: SaveLine[], right: SaveLine[]): SaveRow[] {
  const l = new Map<number, SaveLine>()
  for (const x of left) if (x.id != null) l.set(x.id, x)
  const r = new Map<number, SaveLine>()
  for (const x of right) if (x.id != null) r.set(x.id, x)
  const ids = new Set([...l.keys(), ...r.keys()])
  const rows: SaveRow[] = []
  for (const id of ids) {
    const a = l.get(id)
    const b = r.get(id)
    const ref = (a ?? b) as SaveLine
    rows.push({ id, number: lineNumber(ref.idx ?? 0), zh: ref.zh, left: a ? a.en : null, right: b ? b.en : null })
  }
  return rows.sort((x, y) => x.number - y.number || x.id - y.id)
}

export const isChanged = (r: SaveRow) => r.left !== r.right

// Word-level highlight of `text` against `other` (longest common subsequence),
// so a one-word edit is visible in a long line.
export function wordDiff(text: string, other: string): WordPart[] {
  const a = text.split(/(\s+)/).filter((t) => t !== '')
  const b = other.split(/(\s+)/).filter((t) => t !== '')
  const n = a.length
  const m = b.length
  // Very long lines are shown whole: the table below is n*m.
  if (n * m > 40000) return [{ text, changed: text !== other }]
  const t: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      t[i][j] = a[i] === b[j] ? t[i + 1][j + 1] + 1 : Math.max(t[i + 1][j], t[i][j + 1])
    }
  }
  const out: WordPart[] = []
  const push = (s: string, changed: boolean) => {
    const last = out[out.length - 1]
    if (last && last.changed === changed) last.text += s
    else out.push({ text: s, changed })
  }
  let i = 0
  let j = 0
  while (i < n) {
    if (j < m && a[i] === b[j]) { push(a[i], false); i++; j++ }
    else if (j < m && t[i][j + 1] >= t[i + 1][j]) j++
    else { push(a[i], !/^\s+$/.test(a[i])); i++ }
  }
  return out
}

// Index of the next (dir 1) or previous (dir -1) changed row, wrapping round; -1 when none.
export function stepChanged(rows: SaveRow[], from: number, dir: 1 | -1): number {
  const n = rows.length
  for (let k = 1; k <= n; k++) {
    const i = (((from + dir * k) % n) + n) % n
    if (isChanged(rows[i])) return i
  }
  return -1
}
