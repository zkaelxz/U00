import type { RenameUndo } from '../../../types/characters'

// The last rename's undo is kept for this browser tab's session so a reload
// doesn't lose it; nothing else about it is stored.

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

const memory = new Map<number, RenameUndo>()
const storeKey = (dramaId: number) => `baihe.characters.renameUndo.${dramaId}`

function browserSession(): Store | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function isUndo(x: unknown): x is RenameUndo {
  const u = x as RenameUndo
  return Boolean(u) && typeof u.speaker_label === 'string' && typeof u.previous_label === 'string'
    && Array.isArray(u.previous)
}

export function readRenameUndo(dramaId: number, store: Store | null = browserSession()): RenameUndo | null {
  if (!store) return memory.get(dramaId) ?? null
  try {
    const raw = store.getItem(storeKey(dramaId))
    const v: unknown = raw ? JSON.parse(raw) : null
    return isUndo(v) ? v : null
  } catch {
    return null
  }
}

export function saveRenameUndo(dramaId: number, undo: RenameUndo | null, store: Store | null = browserSession()): void {
  if (!store) {
    if (undo) memory.set(dramaId, undo)
    else memory.delete(dramaId)
    return
  }
  try {
    if (undo) store.setItem(storeKey(dramaId), JSON.stringify(undo))
    else store.removeItem(storeKey(dramaId))
  } catch {
    // Storage full or blocked: the undo is still held in the page.
  }
}

const norm = (x: string) => x.normalize('NFKC').replace(/\p{Cf}/gu, '').toLowerCase().split(/\s+/).filter(Boolean).join(' ')

/** Names that would clash with `label`: every other speaker's label and name. */
export function takenNames(entries: { speaker_label: string; character_name: string; series_character_name?: string }[], label: string): string[] {
  return entries.filter((e) => e.speaker_label !== label).flatMap((e) => [e.speaker_label, e.character_name, e.series_character_name ?? '']).filter(Boolean)
}

/** A plain-English reason the name can't be used, or null when it can. */
export function renameProblem(label: string, name: string, taken: string[]): string | null {
  const n = name.trim()
  if (!n) return 'Type a name.'
  if (n === label) return 'That is already its name.'
  if (n.length > 100) return 'Use 100 characters or fewer.'
  if (n.includes('..') || /[\\/\p{Cc}\p{Cf}\p{Cs}\p{Cn}\p{Co}\p{Zl}\p{Zp}]/u.test(n)) return "Names can't contain slashes, '..' or invisible characters."
  if (taken.some((t) => norm(t) === norm(n))) return 'Another speaker already has that name.'
  return null
}
