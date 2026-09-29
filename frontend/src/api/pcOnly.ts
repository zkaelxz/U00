/*
 * PC-only mode: is this page being viewed on the main PC (loopback), where
 * uploads, deletes, backups and settings are allowed? Shared by every page;
 * read it in components with `usePcOnly()` (hooks/usePcOnly.ts).
 *
 *   'unknown'  /api/meta has not answered yet: render PC-only controls
 *              optimistically (the server still enforces).
 *   'local'    GET /api/meta said local: true.
 *   'remote'   /api/meta said local: false, or a PC-only call got a 403.
 *              The 403 switch is kept in sessionStorage for this tab.
 *
 * Every PC-only mutating call goes through `pcOnlyFetch()`:
 *
 *   postJson('/api/.../delete', { confirm: true }, pcOnlyFetch(f))
 *   postMultipart('/api/.../restore', form, pcOnlyFetch(f))
 *
 * It adds `X-Baihe-Local: 1` (required on local_only POST/PUT/PATCH once
 * #372 lands; harmless before) and flips the mode to 'remote' on a 403.
 * This is only a UI hint: the routes enforce PC-only themselves.
 */
import { api } from './client'
import type { MetaResponse } from './types'

export type PcMode = 'local' | 'remote' | 'unknown'

export const LOCAL_HEADER = 'X-Baihe-Local'
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
  try {
    session()?.setItem(SESSION_KEY, 'remote')
  } catch {
    // Storage blocked: the switch still holds until reload.
  }
  set('remote')
}

/** Apply /api/meta. A 403 seen in this tab wins over local: true. */
export function applyMeta(meta: Pick<MetaResponse, 'local'>): void {
  if (typeof meta.local !== 'boolean') return
  if (!meta.local) set('remote')
  else if (readRemembered() !== 'remote') set('local')
}

let metaLoad: Promise<void> | null = null

/** Fetch /api/meta once per page load; failures leave the mode 'unknown'. */
export function loadPcMode(meta: () => Promise<MetaResponse> = () => api.meta()): Promise<void> {
  metaLoad ??= meta().then(applyMeta, () => undefined)
  return metaLoad
}

/** A fetch for PC-only mutating calls: adds X-Baihe-Local: 1, and a 403 marks the tab remote. */
export function pcOnlyFetch(f: Fetch = fetch): Fetch {
  return async (input, init) => {
    const headers = new Headers(init?.headers)
    headers.set(LOCAL_HEADER, '1')
    const resp = await f(input, { ...init, headers })
    if (resp.status === 403) markRemote()
    return resp
  }
}

/** Test-only: forget the mode and the cached meta load. */
export function resetPcModeForTests(next: PcMode = 'unknown'): void {
  metaLoad = null
  mode = next
  listeners.clear()
}
