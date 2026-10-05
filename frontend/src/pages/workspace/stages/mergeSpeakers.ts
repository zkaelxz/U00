import { ApiError } from '../../../api/client'
import type { CharacterEntry } from '../../../types/translateStage'

export interface MergeChoice {
  label: string
  text: string
  /** A plain-English reason this speaker can't be picked, or null. */
  blocked: string | null
}

/** The speakers `source` can be merged into, in table order. Two speakers
 *  linked to different series characters can't be merged (the server refuses
 *  it too). */
export function mergeChoices(entries: CharacterEntry[], source: CharacterEntry): MergeChoice[] {
  return entries
    .filter((e) => e.speaker_label !== source.speaker_label)
    .map((e) => {
      const name = e.character_name.trim()
      const lines = `${e.line_count} ${e.line_count === 1 ? 'line' : 'lines'}`
      const clash = Boolean(source.series_character_id && e.series_character_id
        && source.series_character_id !== e.series_character_id)
      return {
        label: e.speaker_label,
        text: `${name && name !== e.speaker_label ? `${e.speaker_label} (${name})` : e.speaker_label}, ${lines}`,
        blocked: clash ? 'Linked to a different series character.' : null,
      }
    })
}

/** The confirm-step sentence: how many lines move and where. */
export function mergeSummary(source: CharacterEntry, targetLabel: string): string {
  const n = source.line_count
  const moved = `${n} ${n === 1 ? 'line' : 'lines'}`
  return `${moved} from ${source.speaker_label} will move to ${targetLabel}, and ${source.speaker_label} will be removed from this list. `
    + `Its name, voice and pronouns fill any blanks on ${targetLabel}; anything ${targetLabel} already has is kept.`
}

/** Whether the merge leaves the source's voice clip file behind. The server
 *  moves the voice group (clip, transcript, engine, design) only when the
 *  target has neither a clip nor a designed voice; otherwise the source's file
 *  is orphaned and listed under Library tools > Disk usage. */
export function leavesVoiceClip(source: CharacterEntry, target: CharacterEntry | undefined): boolean {
  if (!source.has_ref_audio || !target) return false
  return target.has_ref_audio || target.voice_design.trim() !== ''
}

export const VOICE_CLIP_NOTE = 'Its voice sample stays on disk. You can remove it later in Library tools > Disk usage.'

/** What the page remembers of the last merge: the server's opaque undo id and
 *  the two labels, for the button and the notice. No Characters row. */
export interface MergeUndoHandle {
  id: string
  source: string
  target: string
  /** Epoch ms after which the server refuses the id. */
  expiresAt: number
}

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

const memory = new Map<number, MergeUndoHandle>()
const storeKey = (dramaId: number) => `baihe.characters.mergeUndo.${dramaId}`

function browserSession(): Store | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function isHandle(x: unknown): x is MergeUndoHandle {
  const u = x as MergeUndoHandle
  return Boolean(u) && typeof u.id === 'string' && typeof u.source === 'string'
    && typeof u.target === 'string' && typeof u.expiresAt === 'number'
}

/** The saved handle, or null when none is saved or it has expired. */
export function readMergeUndo(dramaId: number, now = Date.now(), store: Store | null = browserSession()): MergeUndoHandle | null {
  let found: MergeUndoHandle | null = null
  if (!store) found = memory.get(dramaId) ?? null
  else {
    try {
      const raw = store.getItem(storeKey(dramaId))
      const v: unknown = raw ? JSON.parse(raw) : null
      found = isHandle(v) ? v : null
    } catch {
      found = null
    }
  }
  return found && found.expiresAt > now ? found : null
}

export function saveMergeUndo(dramaId: number, undo: MergeUndoHandle | null, store: Store | null = browserSession()): void {
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

/** Whether the server still holds the undo id after this failure: a "job
 *  running" 409 (nothing was spent) or no answer at all. Not found, a stale
 *  409 and anything else mean the id is gone or can never work. */
export function undoIdSurvives(e: unknown): boolean {
  if (!(e instanceof ApiError)) return true
  const details = e.details as { reason?: unknown } | undefined
  return e.status === 409 && details?.reason === 'job_running'
}
