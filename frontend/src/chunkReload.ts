import { api, withSignal } from './api/client'

// Pages load as separate chunks. Updating the app replaces dist/, so a tab
// opened before the update asks for chunk names that no longer exist; one
// reload picks up the new index.html. The stored time stops a reload loop
// when the chunk is missing for another reason: the second failure is left
// to the page's error boundary. With Baihe itself stopped a reload only
// lands on the browser's error page, so it waits until the server answers.
const KEY = 'baihe.chunkReloadAt'
const WINDOW_MS = 30_000

export function shouldReloadForChunk(storage: Pick<Storage, 'getItem' | 'setItem'> | null, now: number): boolean {
  // Without storage nothing could stop a loop, so leave it to the error boundary.
  if (!storage) return false
  try {
    const last = Number(storage.getItem(KEY) ?? 0)
    if (now - last < WINDOW_MS) return false
    storage.setItem(KEY, String(now))
    return true
  } catch {
    return false
  }
}

// What a failed dynamic import throws, by browser.
const CHUNK_FAILURE = /dynamically imported module|importing a module script failed|loading chunk .* failed|loading css chunk/i

export function isChunkLoadError(error: unknown): boolean {
  return error instanceof Error && CHUNK_FAILURE.test(error.message)
}

export async function serverReachable(): Promise<boolean> {
  try {
    await api.health(withSignal(AbortSignal.timeout(4000)))
    return true
  } catch {
    return false
  }
}

export function installChunkReload(win: Window = window) {
  win.addEventListener('vite:preloadError', () => {
    let storage: Storage | null = null
    try {
      storage = win.sessionStorage
    } catch {
      storage = null
    }
    // Not swallowed: with Baihe stopped the import fails and the error boundary says it could not be reached. With it
    // running, the page is stale and the reload below replaces it.
    void serverReachable().then((up) => {
      if (up && shouldReloadForChunk(storage, Date.now())) win.location.reload()
    })
  })
}
