/*
 * useReadiness(): what is installed on the PC, for controls that must say why they are off.
 *
 *   const { readiness, refresh } = useReadiness()   // readiness is null until it is known
 *
 * One shared read for every control on the page, kept for a few seconds (the server caches
 * its own answer for about as long). `null` (loading, or the read failed) means "unknown":
 * callers treat an unknown answer as ready, so a control is never switched off on a guess;
 * the server still refuses what cannot run. After an install, call `refresh()`.
 */
import { useEffect, useSyncExternalStore } from 'react'

import { getReadiness } from '../api/setup'
import type { SetupReadiness } from '../types/setup'

const FRESH_MS = 4000

let current: SetupReadiness | null = null
let loadedAt = 0
let inflight: Promise<void> | null = null
const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((l) => l())
}

/** Reads again unless a read is under way or the last one is fresh (`force` skips the freshness check). */
export function loadReadiness(force = false): Promise<void> {
  if (inflight) return inflight
  if (!force && current && Date.now() - loadedAt < FRESH_MS) return Promise.resolve()
  inflight = getReadiness()
    .then((r) => {
      current = r
      loadedAt = Date.now()
    }, () => {
      // Keep the last good answer: a failed refresh must not make everything look installed or missing.
    })
    .finally(() => {
      inflight = null
      emit()
    })
  return inflight
}

export const refreshReadiness = () => loadReadiness(true)

/** For tests: forget the shared answer. */
export function resetReadiness() {
  current = null
  loadedAt = 0
  inflight = null
  emit()
}

const subscribe = (l: () => void) => {
  listeners.add(l)
  return () => void listeners.delete(l)
}

export function useReadiness(): { readiness: SetupReadiness | null; refresh: () => Promise<void> } {
  const readiness = useSyncExternalStore(subscribe, () => current, () => current)
  useEffect(() => {
    void loadReadiness()
  }, [])
  return { readiness, refresh: refreshReadiness }
}
