// Pages load as separate chunks. Updating the app replaces dist/, so a tab
// opened before the update asks for chunk names that no longer exist; one
// reload picks up the new index.html. The stored time stops a reload loop
// when the chunk is missing for another reason: the second failure is left
// to the page's error boundary.
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

export function installChunkReload(win: Window = window) {
  win.addEventListener('vite:preloadError', (event) => {
    let storage: Storage | null = null
    try {
      storage = win.sessionStorage
    } catch {
      storage = null
    }
    if (!shouldReloadForChunk(storage, Date.now())) return
    event.preventDefault()
    win.location.reload()
  })
}
