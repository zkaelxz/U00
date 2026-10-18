/*
 * A pop-out is this same app in a second browser window. The window opens with a name that starts with
 * POP_OUT_PREFIX; window.name survives reloads and in-window page changes, so the slim layout needs no URL
 * flag and every route keeps its plain address.
 */
export const POP_OUT_PREFIX = 'baihe-pop-'

export const isPopOutName = (name: string): boolean => name.startsWith(POP_OUT_PREFIX)

export const isPopOutWindow = (): boolean => typeof window !== 'undefined' && isPopOutName(window.name)

// A fresh name each time, so a second pop-out is a new window rather than reusing the first.
export const popOutName = (now: number = Date.now()): string => `${POP_OUT_PREFIX}${now}`

/** Opens `href` (a "#/..." address) in a new window. */
export function openInNewWindow(href: string): void {
  const url = new URL(href, window.location.href).toString()
  // noopener: the new window gets no handle to this one (and window.open returns null whether or not it opened).
  window.open(url, popOutName(), 'popup=yes,width=1100,height=800,noopener')
}
