// Shared state for the glossary extraction runs (From novel, From lines).
// The same run can be shown by more than one panel at once (Glossary →
// From novel and the Source stage's copy, or a panel and the review before
// translating), and each polls on its own. After any panel starts, cancels
// or applies, bumpGlossaryRun(source) makes every mounted panel for that
// source re-read at once and resume polling, so none shows a stale run.
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

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
import { ApiError } from '../../../api/client'
import { getSourceConfig } from '../../../api/source'
import { getGlossaryCatalogues } from '../../../api/translateStage'
import { getNovelStatus } from '../../../api/workspace'
import type { GlossaryCatalogues } from '../../../types/translateStage'
import { ENGINE_CHANGED_TEXT, isActiveStatus, novelGlossaryStartErrorText } from './autotuneGlossary'
import { scopedValue, type GlossarySource, type RunScoped } from './glossaryExtract'
import { useNovelFilesVersion } from './novelFileEvents'
import { useRunStatus } from './useRunStatus'

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

export interface StartOutcome {
  problem: string | null
  error: unknown
  // The run that was started (or attached to); null when none.
  runId: string | null
}

// Starts a source's extraction; every mounted panel for it re-reads. A 409
// is either a run already going (attach to it) or the drama's engine
// changed since the gate checked it. runId is read from the status right
// after the start, so a caller can tell its run from an earlier one.
// Never rejects.
export function startExtraction(dramaId: number, source: GlossarySource): Promise<StartOutcome> {
  const api = GLOSSARY_API[source]
  const ok = (runId: string | null): StartOutcome => {
    bumpGlossaryRun(source)
    return { problem: null, error: null, runId }
  }
  const changed = { problem: ENGINE_CHANGED_TEXT, error: null, runId: null }
  return api.start(dramaId).then(
    () => api.get(dramaId).then((s) => ok(s.run_id), (e: unknown) => ({ ...ok(null), error: e })),
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 409) {
        return api.get(dramaId).then((s) => (isActiveStatus(s.status) ? ok(s.run_id) : changed), () => changed)
      }
      const text = novelGlossaryStartErrorText(e)
      return text ? { problem: text, error: null, runId: null } : { problem: null, error: e, runId: null }
    },
  )
}

// State that belongs to one run's proposals (selection, edits, a pending
// confirm): when a different run is shown -- started here, by another
// panel or in another tab -- it reads as `initial` again.
export function useRunScoped<T>(run: string | null, initial: T): [T, (next: T | ((cur: T) => T)) => void] {
  const [state, setState] = useState<RunScoped<T>>({ run, value: initial })
  const value = scopedValue(state, run, initial)
  const set = (next: T | ((cur: T) => T)) =>
    setState((s) => {
      const cur = scopedValue(s, run, initial)
      return { run, value: typeof next === 'function' ? (next as (c: T) => T)(cur) : next }
    })
  return [value, set]
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
// attach) or a novel file changes (NovelFilePanel). enabled=false skips the
// reads and answers true (the lines source doesn't need a novel).
export function useHasNovel(dramaId: number, reloadKey: unknown, enabled = true): boolean | null {
  const [state, setState] = useState<{ id: number; value: boolean } | null>(null)
  const filesVersion = useNovelFilesVersion()
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    Promise.all([
      getNovelStatus(dramaId).then((s) => s.has_novel_text, () => false),
      getSourceConfig(dramaId).then((c) => c.has_raw_novel_context, () => false),
    ]).then(([text, raw]) => !cancelled && setState({ id: dramaId, value: text || raw }))
    return () => {
      cancelled = true
    }
  }, [dramaId, reloadKey, filesVersion, enabled])
  if (!enabled) return true
  return state && state.id === dramaId ? state.value : null
}
