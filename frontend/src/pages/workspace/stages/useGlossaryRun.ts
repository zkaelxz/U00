// Shared state for the glossary extraction runs (From novel, From lines).
// The same run can be shown by more than one panel at once (the Suggest
// terms bar and the review before translating), and each polls on its own. After any panel starts, cancels
// or applies, bumpGlossaryRun(source) makes every mounted panel for that
// source re-read at once and resume polling, so none shows a stale run.
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'

import {
  applyLinesGlossary,
  applyNovelGlossary,
  cancelLinesGlossary,
  cancelNovelGlossary,
  getLinesGlossary,
  getNovelGlossary,
  startLinesGlossary,
  startNovelGlossary,
} from '../../../api/autotuneGlossary'
import { hasStatus } from '../../../api/client'
import { getReadiness } from '../../../api/export'
import { getSourceConfig } from '../../../api/source'
import { getGlossaryCatalogues } from '../../../api/translateStage'
import { getNovelStatus } from '../../../api/workspace'
import type { GlossaryCatalogues } from '../../../types/translateStage'
import { ENGINE_CHANGED_TEXT, isActiveStatus, novelGlossaryStartErrorText } from './autotuneGlossary'
import { isRecord } from '../../../hooks/useStageDraft'
import { readStored, writeStored } from '../../../hooks/useStoredRun'
import { scopedValue, type Edits, type GlossarySource, type RunScoped } from './glossaryExtract'
import type { ScanStatus, Scans } from './glossaryScan'
import { useNovelFilesVersion } from './novelFileEvents'
import { useRunStatus } from './useRunStatus'
import { browserStorage } from '../../../hooks/usePersistedState'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'
import { useInterval } from '../../../hooks/useInterval'

export const GLOSSARY_API = {
  novel: { get: getNovelGlossary, start: startNovelGlossary, apply: applyNovelGlossary, cancel: cancelNovelGlossary },
  lines: { get: getLinesGlossary, start: startLinesGlossary, apply: applyLinesGlossary, cancel: cancelLinesGlossary },
} as const

const versions: Record<GlossarySource, number> = { novel: 0, lines: 0 }
let termsVersion = 0
const listeners = new Set<() => void>()

export function bumpGlossaryRun(source: GlossarySource): void {
  versions[source] += 1
  listeners.forEach((l) => l())
}

// After proposals were added to the series glossary: the Glossary table
// (GlossaryPanel) re-reads its terms.
export function bumpGlossaryTerms(): void {
  termsVersion += 1
  listeners.forEach((l) => l())
}

const readTerms = () => termsVersion

export function useGlossaryTermsVersion(): number {
  return useSyncExternalStore(subscribe, readTerms, readTerms)
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function useGlossaryRun(dramaId: number, source: GlossarySource) {
  const run = useRunStatus(dramaId, GLOSSARY_API[source].get)
  const read = () => versions[source]
  const version = useSyncExternalStore(subscribe, read, read)
  const seen = useRef(version)
  const { refresh } = run
  useEffect(() => {
    if (seen.current === version) return
    seen.current = version
    refresh()
  }, [version, refresh])
  return run
}

interface StartOutcome {
  problem: string | null
  error: unknown
  // The run that was started (or attached to); null when none.
  runId: string | null
}

// Starts a source's extraction; every mounted panel for it re-reads. A 409
// is either a run already going (attach to it) or the drama's engine
// changed since the gate checked it. runId is read from the status right
// after the start, so a caller can tell its run from an earlier one.
// fresh (novel only) skips the replies cached by an earlier run.
// Never rejects.
export function startExtraction(
  dramaId: number,
  source: GlossarySource,
  opts: { fresh?: boolean } = {},
): Promise<StartOutcome> {
  const api = GLOSSARY_API[source]
  const ok = (runId: string | null): StartOutcome => {
    bumpGlossaryRun(source)
    return { problem: null, error: null, runId }
  }
  const changed = { problem: ENGINE_CHANGED_TEXT, error: null, runId: null }
  const started = source === 'novel' ? startNovelGlossary(dramaId, undefined, opts) : api.start(dramaId)
  return started.then(
    () => api.get(dramaId).then((s) => ok(s.run_id), (e: unknown) => ({ ...ok(null), error: e })),
    (e: unknown) => {
      if (hasStatus(e, 409)) {
        return api.get(dramaId).then((s) => (isActiveStatus(s.status) ? ok(s.run_id) : changed), () => changed)
      }
      const text = novelGlossaryStartErrorText(e)
      return text ? { problem: text, error: null, runId: null } : { problem: null, error: e, runId: null }
    },
  )
}

// Both sources' scans as the server holds them. Reads both, because the one looked at may be
// idle while the other runs, and re-reads when the window is focused or shown again, after any
// panel starts, cancels or applies, and every few seconds while a scan runs, so a scan that
// finished while the person was away is picked up. loaded=false until the first read of this
// drama; a failed read keeps what was known before instead of hiding a running scan.
const SCAN_POLL_MS = 3000

export function useGlossaryScans(dramaId: number): { loaded: boolean; scans: Scans } {
  const [state, setState] = useState<{ id: number; scans: Scans } | null>(null)
  const [tick, setTick] = useState(0)
  const readVersions = () => versions.novel + versions.lines
  const version = useSyncExternalStore(subscribe, readVersions, readVersions)
  useEffect(() => {
    let cancelled = false
    const read = (source: GlossarySource) =>
      GLOSSARY_API[source].get(dramaId).then(
        (s): ScanStatus | null | undefined => (s.status === 'idle' ? null : s),
        () => undefined,
      )
    Promise.all([read('novel'), read('lines')]).then(([novel, lines]) => {
      if (cancelled) return
      setState((cur) => {
        const before = cur && cur.id === dramaId ? cur.scans : { novel: null, lines: null }
        return { id: dramaId, scans: { novel: novel === undefined ? before.novel : novel, lines: lines === undefined ? before.lines : lines } }
      })
    })
    return () => {
      cancelled = true
    }
  }, [dramaId, tick, version])
  useEffect(() => {
    const again = () => {
      if (!document.hidden) setTick((n) => n + 1)
    }
    window.addEventListener('focus', again)
    document.addEventListener('visibilitychange', again)
    return () => {
      window.removeEventListener('focus', again)
      document.removeEventListener('visibilitychange', again)
    }
  }, [])
  const mine = state && state.id === dramaId ? state.scans : null
  const running = !!mine && (isActiveStatus(mine.novel?.status) || isActiveStatus(mine.lines?.status))
  useInterval(() => setTick((n) => n + 1), running ? SCAN_POLL_MS : null, { pauseWhenHidden: true })
  return { loaded: mine !== null, scans: mine ?? { novel: null, lines: null } }
}

// State that belongs to one run's proposals (selection, edits, a pending
// confirm): when a different run is shown -- started here, by another
// panel or in another tab -- it reads as `initial` again.
// With `keep`, the value is also remembered in the browser together with its
// run_id, so leaving the page and coming back to the same run restores it; a
// copy that names another run is ignored.
interface KeepRunScoped<T> {
  key: string
  encode: (v: T) => unknown
  decode: (v: unknown) => T | null
}

const isStoredScope = (v: unknown): v is { run: string; value: unknown } =>
  typeof v === 'object' && v !== null && typeof (v as { run?: unknown }).run === 'string'

export function useRunScoped<T>(run: string | null, initial: T, keep?: KeepRunScoped<T>): [T, (next: T | ((cur: T) => T)) => void] {
  // Nothing set yet: the value is the remembered copy of this run, if any, else `initial`.
  const [state, setState] = useState<RunScoped<T> | null>(null)
  const remembered = useMemo(() => {
    if (!keep || run === null) return null
    const stored = readStored(browserStorage(), keep.key, isStoredScope)
    return stored && stored.run === run ? keep.decode(stored.value) : null
    // `keep` is a fresh literal each render; its key names the slot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, keep?.key])
  const fallback = remembered ?? initial
  const value = state ? scopedValue(state, run, fallback) : fallback
  const set = (next: T | ((cur: T) => T)) => {
    const cur = value
    const v = typeof next === 'function' ? (next as (c: T) => T)(cur) : next
    if (keep && run !== null) writeStored(browserStorage(), keep.key, { run, value: keep.encode(v) })
    setState({ run, value: v })
  }
  return [value, set]
}

const pickedKeep = (key: string): KeepRunScoped<Set<string> | null> => ({
  key,
  encode: (v) => (v === null ? null : [...v]),
  decode: (v) => (Array.isArray(v) && v.every((t) => typeof t === 'string') ? new Set<string>(v) : null),
})

const editsKeep = (key: string): KeepRunScoped<Edits> => ({
  key,
  encode: (v) => v,
  decode: (v) => (isRecord(v) ? (v as Edits) : null),
})

/** The proposal ticks and edits of the run shown, remembered per title and source. */
export function useRunSelection(dramaId: number, source: GlossarySource, run: string | null) {
  const slot = `glossary.${dramaId}.${source}`
  const [picked, setPicked] = useRunScoped<Set<string> | null>(run, null, pickedKeep(`${slot}.picked`))
  const [edits, setEdits] = useRunScoped<Edits>(run, {}, editsKeep(`${slot}.edits`))
  return { picked, setPicked, edits, setEdits }
}

// Categories and policies for the proposal edit selects; null until read
// (only once enabled) or if the read failed.
export function useGlossaryCatalogues(enabled: boolean): GlossaryCatalogues | null {
  const [catalogues, setCatalogues] = useState<GlossaryCatalogues | null>(null)
  useEffect(() => {
    if (!enabled || catalogues) return
    let cancelled = false
    getGlossaryCatalogues().then((c) => !cancelled && setCatalogues(c), () => undefined)
    return () => {
      cancelled = true
    }
  }, [enabled, catalogues])
  return catalogues
}

// Whether the drama has novel text or a saved original-language novel.
// null while reading. Re-reads when the drama is refetched (NovelPanel's
// attach) or a novel file changes (NovelFilePanel).
export function useHasNovel(dramaId: number, reloadKey: unknown): boolean | null {
  const [state, setState] = useState<{ id: number; value: boolean } | null>(null)
  const filesVersion = useNovelFilesVersion()
  useCancellableEffect((cancelled) => {
    Promise.all([
      getNovelStatus(dramaId).then((s) => s.has_novel_text, () => false),
      getSourceConfig(dramaId).then((c) => c.has_raw_novel_context, () => false),
    ]).then(([text, raw]) => !cancelled() && setState({ id: dramaId, value: text || raw }))
  }, [dramaId, reloadKey, filesVersion])
  return state && state.id === dramaId ? state.value : null
}

// Whether the drama has transcript lines to read. null while reading; a
// failed read answers true so the server decides when the run starts.
export function useHasLines(dramaId: number): boolean | null {
  const [state, setState] = useState<{ id: number; value: boolean } | null>(null)
  useCancellableEffect((cancelled) => {
    getReadiness(dramaId).then(
      (r) => !cancelled() && setState({ id: dramaId, value: r.total_lines > 0 }),
      () => !cancelled() && setState({ id: dramaId, value: true }),
    )
  }, [dramaId])
  return state && state.id === dramaId ? state.value : null
}
