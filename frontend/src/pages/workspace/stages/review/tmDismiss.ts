import { useEffect, useState } from 'react'

import type { TmSuggestion } from '../../../../types/review'

// "Dismiss" hides a translation-memory suggestion for this browser tab's
// session only (nothing is written to the server). Keyed by the line's source text and the suggested translation, so
// the same pair on another line is hidden too, and a new suggestion shows.

type Store = Pick<Storage, 'getItem' | 'setItem'>

// Used when sessionStorage is unavailable (private mode, blocked site data).
const memory = new Map<number, Set<string>>()

const storeKey = (dramaId: number) => `baihe.review.tmDismissed.${dramaId}`

export const tmDismissKey = (s: Pick<TmSuggestion, 'zh' | 'suggestion'>) => `${s.zh.trim()}\u0000${s.suggestion}`

function browserSession(): Store | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

export function readDismissed(dramaId: number, store: Store | null = browserSession()): Set<string> {
  if (!store) return new Set(memory.get(dramaId))
  try {
    const raw = store?.getItem(storeKey(dramaId))
    const list: unknown = raw ? JSON.parse(raw) : []
    return new Set(Array.isArray(list) ? list.filter((x): x is string => typeof x === 'string') : [])
  } catch {
    return new Set()
  }
}

export function dismissTm(dramaId: number, s: TmSuggestion, store: Store | null = browserSession()): void {
  const set = readDismissed(dramaId, store)
  set.add(tmDismissKey(s))
  if (!store) {
    memory.set(dramaId, set)
    return
  }
  try {
    store.setItem(storeKey(dramaId), JSON.stringify([...set]))
  } catch {
    // Storage full or blocked: the suggestion shows again after a reload.
  }
}

// Both the line list and the Records list hide a dismissed suggestion at once.
const EVENT = 'baihe:tm-dismissed'

export function dismissTmEverywhere(dramaId: number, s: TmSuggestion): void {
  dismissTm(dramaId, s)
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT, { detail: dramaId }))
}

export function useTmDismissed(dramaId: number): Set<string> {
  const [state, setState] = useState(() => ({ dramaId, set: readDismissed(dramaId) }))
  useEffect(() => {
    const on = (e: Event) => {
      if ((e as CustomEvent<number>).detail === dramaId) setState({ dramaId, set: readDismissed(dramaId) })
    }
    window.addEventListener(EVENT, on)
    return () => window.removeEventListener(EVENT, on)
  }, [dramaId])
  // Another drama than the one in state: read its set now.
  return state.dramaId === dramaId ? state.set : readDismissed(dramaId)
}

export function visibleTm(list: TmSuggestion[], dismissed: Set<string>): TmSuggestion[] {
  return list.filter((s) => !dismissed.has(tmDismissKey(s)))
}
