/*
 * PC-only mode: is this page being viewed on the main PC (loopback), where
 * uploads, deletes, backups and settings are allowed? Shared by every page;
 * read it in components with `usePcOnly()` (hooks/usePcOnly.ts).
 *
 *   'unknown'  /api/meta has not answered yet: render PC-only controls
 *              optimistically (the server still enforces).
 *   'local'    GET /api/meta said local: true.
 *   'remote'   /api/meta said local: false, or a PC-only call got a 403.
 *              The 403 switch is kept in sessionStorage for this tab until
 *              the next page load's /api/meta says local: true.
 *
 * Every PC-only mutating call goes through `pcOnlyFetch()`:
 *
 *   postJson('/api/.../delete', { confirm: true }, pcOnlyFetch(f))
 *   postMultipart('/api/.../restore', form, pcOnlyFetch(f))
 *
 * It adds `X-Baihe-Local: 1` (client.ts LOCAL_HEADER; the server requires
 * JSON or this header on local_only POST/PUT/PATCH) and flips the mode to
 * 'remote' on a 403.
 * This is only a UI hint: the routes enforce PC-only themselves.
 */
import { LOCAL_HEADER, api } from './client'
import type { MetaResponse } from './types'

export type PcMode = 'local' | 'remote' | 'unknown'

const SESSION_KEY = 'baihe.pcOnly'

type Fetch = typeof fetch

function session(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage
  } catch {
    return null
  }
}

function readRemembered(): PcMode {
  try {
    return session()?.getItem(SESSION_KEY) === 'remote' ? 'remote' : 'unknown'
  } catch {
    return 'unknown'
  }
}

let mode: PcMode = readRemembered()
// A 403 seen during this page load: stays remote even if meta says local.
let refusedThisLoad = false
const listeners = new Set<() => void>()

function set(next: PcMode) {
  if (next === mode) return
  mode = next
  listeners.forEach((l) => l())
}

export function getPcMode(): PcMode {
  return mode
}

export function subscribePcMode(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** A PC-only call was refused: hide PC-only controls for the rest of this tab. */
export function markRemote(): void {
  refusedThisLoad = true
  try {
    session()?.setItem(SESSION_KEY, 'remote')
  } catch {
    // Storage blocked: the switch still holds until reload.
  }
  set('remote')
}

/**
 * Apply /api/meta. local: true clears a remote remembered from a 403 on an
 * earlier page load (so one stray 403 doesn't stick), but not one seen
 * during this page load.
 */
export function applyMeta(meta: Pick<MetaResponse, 'local'>): void {
  if (typeof meta.local !== 'boolean') return
  if (!meta.local) set('remote')
  else if (!refusedThisLoad) {
    try {
      session()?.removeItem(SESSION_KEY)
    } catch {
      // Storage blocked: nothing remembered anyway.
    }
    set('local')
  }
}

let metaLoad: Promise<void> | null = null
// /api/meta failed this page load (the mode then stays 'unknown').
let metaFailed = false

export function getPcMetaFailed(): boolean {
  return metaFailed
}

/** Fetch /api/meta once per page load; failures leave the mode 'unknown'. */
export function loadPcMode(meta: () => Promise<MetaResponse> = () => api.meta()): Promise<void> {
  metaLoad ??= meta().then(applyMeta, () => {
    metaFailed = true
    listeners.forEach((l) => l())
  })
  return metaLoad
}

/** A fetch for PC-only mutating calls: adds X-Baihe-Local: 1, and a 403 marks the tab remote. */
export function pcOnlyFetch(f: Fetch = fetch): Fetch {
  return async (input, init) => {
    const headers = new Headers(init?.headers)
    for (const [k, v] of Object.entries(LOCAL_HEADER)) headers.set(k, v)
    const resp = await f(input, { ...init, headers })
    if (resp.status === 403) markRemote()
    return resp
  }
}

/** Test-only: forget the mode and the cached meta load. */
export function resetPcModeForTests(next: PcMode = 'unknown'): void {
  metaLoad = null
  metaFailed = false
  refusedThisLoad = false
  mode = next
  listeners.clear()
}
