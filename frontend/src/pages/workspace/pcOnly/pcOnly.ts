// Private stand-in for the shared hooks/usePcOnly.ts + errorMessages copy
// being built on branch react-library-admin. Swap to the shared helper when
// both land. Same shape: usePcOnly() -> boolean | null.

import { useEffect, useState } from 'react'

import { getJson } from '../../../api/client'

type Fetch = typeof fetch

// /api/meta's `local` field (true when the page is viewed on the PC itself).
// Missing (older API) or unreadable counts as not local.
export async function fetchIsLocal(f: Fetch = fetch): Promise<boolean> {
  try {
    const meta = await getJson<{ local?: unknown }>('/api/meta', f)
    return meta?.local === true
  } catch {
    return false
  }
}

// One read per page load, shared by every panel; markRemote() (after a 403
// from a PC-only call) flips everyone to remote.
let cached: boolean | null = null
let pending: Promise<boolean> | null = null
const listeners = new Set<(v: boolean) => void>()

function publish(v: boolean) {
  cached = v
  listeners.forEach((l) => l(v))
}

export function markRemote() {
  publish(false)
}

// Test hook: forget the cached answer.
export function resetPcOnlyCache() {
  cached = null
  pending = null
}

// true = on the PC (show PC-only controls), false = remote (hide them),
// null = not known yet (hide them until known).
export function usePcOnly(): boolean | null {
  const [value, setValue] = useState<boolean | null>(cached)
  useEffect(() => {
    listeners.add(setValue)
    if (cached === null) {
      pending ??= fetchIsLocal()
      pending.then((v) => {
        if (cached === null) publish(v)
        else setValue(cached)
      })
    }
    return () => {
      listeners.delete(setValue)
    }
  }, [])
  return value
}

// Plain text for the two errors a PC-only delete expects; null means "use
// the generic ErrorBanner".
export function pcOnlyErrorText(err: unknown): string | null {
  const e = err as { status?: number; code?: string } | null
  if (!e) return null
  if (e.code === 'forbidden' || e.status === 403) return 'This only works on the main PC.'
  if (e.code === 'conflict' || e.status === 409) return 'Wait for the running job to finish.'
  return null
}

// Routes a PC-only call's failure: a 403 also switches every panel to remote
// mode; forbidden/conflict get plain text, anything else the ErrorBanner.
export function reportPcOnlyError(
  err: unknown,
  setText: (t: string) => void,
  setError: (e: unknown) => void,
) {
  const e = err as { status?: number; code?: string } | null
  if (e?.status === 403 || e?.code === 'forbidden') markRemote()
  const text = pcOnlyErrorText(err)
  if (text) setText(text)
  else setError(err)
}

// Two-step confirm state: the first press arms, the second fires; a timeout,
// Cancel, Escape or blur disarm. Pure so it is unit-testable.
export type ConfirmEvent = 'press' | 'timeout' | 'cancel'
export function confirmStep(armed: boolean, event: ConfirmEvent): { armed: boolean; fire: boolean } {
  if (event === 'press') return armed ? { armed: false, fire: true } : { armed: true, fire: false }
  return { armed: false, fire: false }
}

export const CONFIRM_TIMEOUT_MS = 5000

export const PC_ONLY_NOTE = 'Deleting is PC only.'
