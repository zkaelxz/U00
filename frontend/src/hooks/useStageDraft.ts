import { useCallback, useMemo } from 'react'

/*
 * useStageDraft: one per-drama store for a stage form's local, not-yet-run
 * values (language, engine, style, advanced knobs, prompt override...), so the
 * form reads the same after a stage-tab switch, a page navigation or a reload.
 *
 *   const { draft, save, clear } = useStageDraft(dramaId, 'dub', DUB_DRAFT_SHAPE)
 *   const [form, setForm] = useState(() => ({ ...defaults, ...draft }))
 *   useEffect(() => save(form), [save, form])
 *   // "Reset to defaults": setForm(defaults); clear()
 *
 * The server's run settings stay the record of what a run used; the draft only
 * fills the form until the user runs or resets it. Keys never hold secrets.
 *
 * localStorage, key "baihe.draft.<dramaId>.<stage>", JSON envelope
 * { v, values }. A different `v` reads as no draft, so a shape change bumps
 * DRAFT_VERSION instead of migrating. Storage may be missing or throw (private
 * window, blocked site data): every access is wrapped and the form then just
 * starts from its defaults.
 */

export const DRAFT_KEY_PREFIX = 'baihe.draft.'
export const DRAFT_VERSION = 1

export type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export function draftStorage(): DraftStorage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

export const draftKey = (dramaId: number, stage: string) => `${DRAFT_KEY_PREFIX}${dramaId}.${stage}`

export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

/** The stored values for this drama and stage, or null when absent, corrupt or from another version. */
export function readDraft(storage: DraftStorage | null, dramaId: number, stage: string): Record<string, unknown> | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(draftKey(dramaId, stage))
    if (raw === null) return null
    const env: unknown = JSON.parse(raw)
    if (!isRecord(env) || env.v !== DRAFT_VERSION || !isRecord(env.values)) return null
    return env.values
  } catch {
    return null
  }
}

/** Remember the form; false when storage is unavailable. */
export function writeDraft(storage: DraftStorage | null, dramaId: number, stage: string, values: object): boolean {
  if (!storage) return false
  try {
    storage.setItem(draftKey(dramaId, stage), JSON.stringify({ v: DRAFT_VERSION, values }))
    return true
  } catch {
    return false
  }
}

export function clearDraft(storage: DraftStorage | null, dramaId: number, stage: string): boolean {
  if (!storage) return false
  try {
    storage.removeItem(draftKey(dramaId, stage))
    return true
  } catch {
    return false
  }
}

/**
 * The keys of `shape` that the draft holds with the same type, so a stored
 * value of the wrong type (or from an older form) falls back to the default.
 * Shallow: a nested object is kept only when it is an object.
 */
export function pickDraft<T extends object>(raw: Record<string, unknown> | null, shape: T): Partial<T> {
  const out: Partial<T> = {}
  if (!raw) return out
  for (const key of Object.keys(shape) as (keyof T & string)[]) {
    const want = shape[key]
    const have = raw[key]
    if (have === undefined || have === null) continue
    if (Array.isArray(want) ? Array.isArray(have) : typeof have === typeof want && !Array.isArray(have)) {
      out[key] = have as T[typeof key]
    }
  }
  return out
}

/** The keys of `current` that differ from `base`: what a draft keeps of a form the server also saves. */
export function changedKeys<T extends object>(current: T, base: T): Partial<T> {
  const out: Partial<T> = {}
  for (const key of Object.keys(current) as (keyof T)[]) if (current[key] !== base[key]) out[key] = current[key]
  return out
}

export interface StageDraft<T extends object> {
  // The remembered values that match `shape` (empty when there is no draft).
  draft: Partial<T>
  // Everything stored, for forms that validate their own shape.
  raw: Record<string, unknown> | null
  save: (values: object) => void
  clear: () => void
}

export function useStageDraft<T extends object>(dramaId: number, stage: string, shape: T): StageDraft<T> {
  // Read once per drama and stage: the form owns the values from then on.
  const raw = useMemo(() => readDraft(draftStorage(), dramaId, stage), [dramaId, stage])
  const draft = useMemo(() => pickDraft(raw, shape), [raw, shape])
  const save = useCallback((values: object) => void writeDraft(draftStorage(), dramaId, stage, values), [dramaId, stage])
  const clear = useCallback(() => void clearDraft(draftStorage(), dramaId, stage), [dramaId, stage])
  return { draft, raw, save, clear }
}
