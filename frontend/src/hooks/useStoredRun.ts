import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { useReattachJob } from './useReattachJob'
import { draftStorage, useStageDraft, type DraftStorage } from './useStageDraft'

/*
 * Results and forms that survive leaving a page and coming back.
 *
 * useStoredRun covers a per-title panel that runs a (paid or long) background job:
 *   - reattaches to the job still running server-side (fixed per-drama job id),
 *   - fetches the result the server kept for the title and hands it to onResult,
 *   - remembers the form in the stage draft store (useStageDraft).
 * useStoredState covers what has no job or title behind it (assistant answers, a
 * Reader tool's last result, the quick-translate text): a value kept in the browser.
 *
 * The browser copy is localStorage "baihe.run.<key>", JSON envelope { v, value }; a
 * different `v` reads as nothing, so a shape change bumps RUN_VERSION. Values over
 * `maxChars` are not stored (and drop the older copy, so a stale one never comes
 * back). Storage may be missing or throw: every access is wrapped and the page
 * works without it. clearStoredRuns() runs at sign-out, so the next person on this
 * browser doesn't see the last one's answers.
 */

export const RUN_KEY_PREFIX = 'baihe.run.'
export const RUN_VERSION = 1
export const DEFAULT_MAX_CHARS = 100_000

export function readStored<T>(storage: DraftStorage | null, key: string, valid: (v: unknown) => v is T): T | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(RUN_KEY_PREFIX + key)
    if (raw === null) return null
    const env: unknown = JSON.parse(raw)
    if (typeof env !== 'object' || env === null || (env as { v?: unknown }).v !== RUN_VERSION) return null
    const value = (env as { value?: unknown }).value
    return valid(value) ? value : null
  } catch {
    return null
  }
}

/** Remember a value; false when it is too big or storage is unavailable (the older copy is dropped either way). */
export function writeStored(storage: DraftStorage | null, key: string, value: unknown, maxChars = DEFAULT_MAX_CHARS): boolean {
  if (!storage) return false
  try {
    const text = JSON.stringify({ v: RUN_VERSION, value })
    if (text.length > maxChars) {
      storage.removeItem(RUN_KEY_PREFIX + key)
      return false
    }
    storage.setItem(RUN_KEY_PREFIX + key, text)
    return true
  } catch {
    return false
  }
}

export function removeStored(storage: DraftStorage | null, key: string): void {
  try {
    storage?.removeItem(RUN_KEY_PREFIX + key)
  } catch {
    // Nothing to remove from a store that cannot be read.
  }
}

/** Forget every stored result and the stage drafts; called when the person signs out. */
export function clearStoredRuns(storage: Storage | null = draftStorage() as Storage | null): void {
  if (!storage) return
  try {
    const keys: string[] = []
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i)
      if (k && (k.startsWith(RUN_KEY_PREFIX) || k.startsWith('baihe.draft.'))) keys.push(k)
    }
    keys.forEach((k) => storage.removeItem(k))
  } catch {
    // Blocked storage holds nothing of ours.
  }
}

/**
 * A value kept in the browser under `key` (null key: not kept). `initial` means
 * "nothing": it is not stored, and storing it again forgets the older copy.
 * The setter takes a value or an updater, like useState. `revive` fixes up what was
 * read (e.g. a request that was still running when the page closed).
 */
export function useStoredState<T>(
  key: string | null,
  initial: T,
  valid: (v: unknown) => v is T,
  { maxChars = DEFAULT_MAX_CHARS, revive }: { maxChars?: number; revive?: (v: T) => T } = {},
): [T, (next: T | ((cur: T) => T)) => void] {
  const load = (k: string | null): T => {
    const stored = k === null ? null : readStored(draftStorage(), k, valid)
    return stored === null ? initial : revive ? revive(stored) : stored
  }
  const [state, setState] = useState<{ key: string | null; value: T }>(() => ({ key, value: load(key) }))
  // A different key (another title) reads its own copy; the old value never carries over.
  const value = state.key === key ? state.value : load(key)
  useEffect(() => {
    if (state.key !== key || key === null) return
    if (state.value === initial) removeStored(draftStorage(), key)
    else writeStored(draftStorage(), key, state.value, maxChars)
  }, [state, key, initial, maxChars])
  const valueRef = useRef(value)
  useEffect(() => {
    valueRef.current = value
  })
  const set = useCallback(
    (next: T | ((cur: T) => T)) => {
      const v = typeof next === 'function' ? (next as (c: T) => T)(valueRef.current) : next
      valueRef.current = v
      setState({ key, value: v })
    },
    [key],
  )
  return [value, set]
}

// Framework-free (testable without a DOM): reads the stored result once and hands it
// on unless cancelled; a 404 is "none yet", not an error. Returns the cancel function.
export function loadStoredResult<R>(
  dramaId: number,
  h: { fetchResult: (id: number) => Promise<R>; onResult: (r: R) => void; onError?: (e: unknown) => void },
): () => void {
  let cancelled = false
  h.fetchResult(dramaId).then(
    (r) => !cancelled && h.onResult(r),
    (e: unknown) => {
      if (cancelled || (e instanceof ApiError && e.status === 404)) return
      h.onError?.(e)
    },
  )
  return () => {
    cancelled = true
  }
}

interface StoredRunOptions<R, D extends object> {
  dramaId: number
  // Names the form draft ("baihe.draft.<dramaId>.<kind>").
  kind: string
  // The panel's fixed per-drama job ids (what useReattachJob gets).
  jobIds: readonly string[]
  adopt: (id: string) => void
  // Reads the result the server keeps for the title; a 404 means there is none.
  // `draft` is the remembered form, for results the server keys by the form (mode, model).
  fetchResult: (dramaId: number, draft: Partial<D>) => Promise<R>
  // Gets the stored result once on mount. A run the panel started meanwhile
  // must win: use setResult((cur) => cur ?? r).
  onResult: (result: R) => void
  onError?: (e: unknown) => void
  draftShape: D
}

export function useStoredRun<R, D extends object>(opts: StoredRunOptions<R, D>) {
  const { dramaId, kind, jobIds, adopt, draftShape } = opts
  useReattachJob(jobIds, adopt)
  const stage = useStageDraft(dramaId, kind, draftShape)
  const latest = useRef(opts)
  useEffect(() => {
    latest.current = opts
  })
  useEffect(
    () =>
      loadStoredResult(dramaId, {
        fetchResult: (id) => latest.current.fetchResult(id, stage.draft),
        onResult: (r) => latest.current.onResult(r),
        onError: (e) => latest.current.onError?.(e),
      }),
    [dramaId, stage.draft],
  )
  return stage
}
