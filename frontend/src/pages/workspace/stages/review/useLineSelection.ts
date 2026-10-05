import { useMemo, useRef, useSyncExternalStore } from 'react'

export interface VisibleLine {
  id: number
  // The number shown to the user (`lineNumber(idx)`), for "#12, #14-#18".
  number: number
}

const EMPTY: ReadonlySet<number> = new Set()

// The selection itself, free of React so it can be tested directly. `ids` is
// replaced (never mutated) on every change, which is what the hook subscribes to.
export class LineSelectionStore {
  ids: ReadonlySet<number> = EMPTY
  private scope: string | number
  private visible: number[] = []
  private numbers = new Map<number, number>()
  private anchor: number | null = null
  private listeners = new Set<() => void>()

  constructor(scope: string | number) {
    this.scope = scope
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn)
    return () => void this.listeners.delete(fn)
  }
  getSnapshot = () => this.ids

  private set(next: ReadonlySet<number>) {
    if (next === this.ids) return
    this.ids = next
    this.listeners.forEach((fn) => fn())
  }

  /** Another title starts with nothing selected. Silent: the caller is rendering with the new scope. */
  setScope(scope: string | number) {
    if (scope === this.scope) return
    this.scope = scope
    this.ids = EMPTY
    this.visible = []
    this.numbers = new Map()
    this.anchor = null
  }

  /** The lines shown, in order; ranges and line numbers resolve against them. */
  setVisible = (lines: VisibleLine[]) => {
    this.visible = lines.map((l) => l.id)
    for (const l of lines) this.numbers.set(l.id, l.number)
  }

  toggle = (id: number) => {
    this.anchor = id
    const next = new Set(this.ids)
    if (!next.delete(id)) next.add(id)
    this.set(next)
  }

  selectMany = (list: number[]) => {
    if (list.every((id) => this.ids.has(id))) return
    this.set(new Set([...this.ids, ...list]))
  }

  /** Every line from `fromId` to `toId` inclusive, in the order currently shown. */
  selectRange = (fromId: number, toId: number) => {
    const from = this.visible.indexOf(fromId)
    const to = this.visible.indexOf(toId)
    this.anchor = toId
    if (from === -1 || to === -1) this.selectMany([toId])
    else this.selectMany(this.visible.slice(Math.min(from, to), Math.max(from, to) + 1))
  }

  /** A tick-box click: a range from the last ticked line when `range` (Shift), else a toggle. */
  tick = (id: number, range: boolean) => {
    if (range && this.anchor !== null && this.visible.includes(this.anchor)) this.selectRange(this.anchor, id)
    else this.toggle(id)
  }

  clear = () => {
    this.anchor = null
    this.set(EMPTY)
  }

  /** Line numbers for `ids`, ascending; ids never shown are left out. */
  lineNumbers = (list: number[]) => list.flatMap((id) => this.numbers.get(id) ?? []).sort((a, b) => a - b)

  /** Ascending by line number, so the order does not depend on tick order. */
  sorted(): number[] {
    return [...this.ids].sort((a, b) => (this.numbers.get(a) ?? Infinity) - (this.numbers.get(b) ?? Infinity))
  }
}

export interface LineSelection {
  /** Selected line ids, ascending by line number. Ids, not positions, so a filter, search or page change keeps them. */
  selectedIds: number[]
  selectedSet: ReadonlySet<number>
  count: number
  toggle: (id: number) => void
  selectRange: (fromId: number, toId: number) => void
  selectMany: (ids: number[]) => void
  clear: () => void
  tick: (id: number, range: boolean) => void
  setVisible: (lines: VisibleLine[]) => void
  lineNumbers: (ids: number[]) => number[]
}

// Selected lines for one title. `scope` is the title's id: a new scope starts
// with nothing selected, so a selection never carries into another title.
export function useLineSelection(scope: string | number): LineSelection {
  const store = useRef<LineSelectionStore>(null)
  if (!store.current) store.current = new LineSelectionStore(scope)
  const s = store.current
  s.setScope(scope)
  const ids = useSyncExternalStore(s.subscribe, s.getSnapshot)
  return useMemo(
    () => ({
      selectedIds: s.sorted(), selectedSet: ids, count: ids.size,
      toggle: s.toggle, selectRange: s.selectRange, selectMany: s.selectMany, clear: s.clear,
      tick: s.tick, setVisible: s.setVisible, lineNumbers: s.lineNumbers,
    }),
    [s, ids],
  )
}

/** "#12, #14-#18": runs of consecutive line numbers collapse to a range. */
export function formatLineNumbers(numbers: number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b)
  const parts: string[] = []
  for (let i = 0; i < sorted.length; ) {
    let j = i
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j += 1
    parts.push(j > i ? `#${sorted[i]}-#${sorted[j]}` : `#${sorted[i]}`)
    i = j + 1
  }
  return parts.join(', ')
}
