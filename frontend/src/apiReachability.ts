// Owns the poll behind the header's "Can't reach Baihe" badge.

const API_STATUS_RECHECK_MS = 30_000
const API_STATUS_CONFIRM_MS = 3_000

export type ApiState = 'unknown' | 'up' | 'down'

// A phone suspends the page's network when another app is in front, so a check that fails there says nothing
// about the server: a failure only counts after a second try while the page is visible, and coming back to the
// front (focus, visibilitychange, online, pageshow: a WebView fires only some of them) re-checks at once.
export function watchApiReachability(
  probe: () => Promise<unknown>,
  setState: (state: ApiState) => void,
  win: Pick<Window, 'addEventListener' | 'removeEventListener'>,
  doc: Pick<Document, 'addEventListener' | 'removeEventListener' | 'visibilityState'>,
): () => void {
  let stopped = false
  let failedOnce = false
  let retry: ReturnType<typeof setTimeout> | undefined
  const check = () => {
    if (doc.visibilityState === 'hidden') {
      failedOnce = false
      return
    }
    probe().then(
      () => {
        if (stopped) return
        failedOnce = false
        setState('up')
      },
      () => {
        // A check that was in flight when the app went to the background fails for that reason alone.
        if (stopped || doc.visibilityState === 'hidden') return
        if (failedOnce) return setState('down')
        failedOnce = true
        retry ??= setTimeout(() => {
          retry = undefined
          check()
        }, API_STATUS_CONFIRM_MS)
      },
    )
  }
  const recheck = () => check()
  check()
  const timer = setInterval(recheck, API_STATUS_RECHECK_MS)
  const winEvents = ['focus', 'online', 'pageshow']
  winEvents.forEach((e) => win.addEventListener(e, recheck))
  doc.addEventListener('visibilitychange', recheck)
  return () => {
    stopped = true
    clearTimeout(retry)
    clearInterval(timer)
    winEvents.forEach((e) => win.removeEventListener(e, recheck))
    doc.removeEventListener('visibilitychange', recheck)
  }
}
