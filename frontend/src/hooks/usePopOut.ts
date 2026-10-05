import { useEffect, useState } from 'react'

import { isTextEntry } from './useShortcut'

// Document Picture-in-Picture: an always-on-top window that holds a normal DOM
// element. Chromium desktop only, so every call is feature-detected.
interface PipApi {
  requestWindow: (options?: { width?: number; height?: number }) => Promise<Window>
}

function pipApi(): PipApi | null {
  if (typeof window === 'undefined') return null
  const api = (window as unknown as { documentPictureInPicture?: PipApi }).documentPictureInPicture
  return api && typeof api.requestWindow === 'function' ? api : null
}

export function popOutSupported(): boolean {
  return pipApi() !== null
}

// Stylesheets are not shared with a PiP window: copy every readable rule set,
// and fall back to a <link> for sheets the browser won't let us read.
export function copyStyles(from: Document, to: Document): void {
  for (const sheet of Array.from(from.styleSheets)) {
    try {
      const style = to.createElement('style')
      style.textContent = Array.from(sheet.cssRules, (r) => r.cssText).join('\n')
      to.head.appendChild(style)
    } catch {
      if (sheet.href) {
        const link = to.createElement('link')
        link.rel = 'stylesheet'
        link.href = sheet.href
        to.head.appendChild(link)
      }
    }
  }
  // Theme tokens hang off the root element's class and data attributes.
  const src = from.documentElement
  const dst = to.documentElement
  dst.className = src.className
  for (const a of Array.from(src.attributes)) if (a.name.startsWith('data-')) dst.setAttribute(a.name, a.value)
}

// Keys pressed in the floating window go to the page's shortcuts, except while typing.
function forwardKeys(win: Window, to: Document): void {
  const forward = (e: KeyboardEvent) => {
    if (isTextEntry(e.target)) return
    const copy = new KeyboardEvent(e.type, { key: e.key, code: e.code, altKey: e.altKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, shiftKey: e.shiftKey, repeat: e.repeat, bubbles: true, cancelable: true })
    to.dispatchEvent(copy)
    if (copy.defaultPrevented) e.preventDefault()
  }
  win.document.addEventListener('keydown', forward)
}

export interface PopOut {
  setRestore: (fn: () => void) => void
  open: () => Promise<void>
  close: () => void
  // Puts the element back (if it is out) and closes the window; for unmount.
  dispose: () => void
}

// Moves `box` (never a copy, so a playing video keeps its position, rate and
// volume) into a floating window, and calls `restore` to put it back when that
// window closes or on dispose.
export function createPopOut(box: HTMLElement, initialRestore: () => void, onActive: (active: boolean) => void, getApi: () => PipApi | null = pipApi): PopOut {
  let win: Window | null = null
  let restore = initialRestore
  const give = () => {
    if (!win) return
    win = null
    onActive(false)
    restore()
  }
  return {
    setRestore(fn) {
      restore = fn
    },
    async open() {
      const api = getApi()
      if (!api || win) return
      try {
        const w = await api.requestWindow({ width: 640, height: 520 })
        copyStyles(document, w.document)
        w.document.documentElement.classList.add('review-popout')
        w.document.body.style.margin = '0'
        w.document.body.style.padding = '8px'
        w.document.body.appendChild(box)
        forwardKeys(w, document)
        w.addEventListener('pagehide', give, { once: true })
        win = w
        onActive(true)
      } catch {
        // Refused (no user gesture, or blocked): the player simply stays on the page.
      }
    },
    close() {
      const w = win
      w?.close()
      // pagehide restores; close() on an already-dead window may not fire it.
      give()
    },
    dispose() {
      const w = win
      give()
      w?.close()
    },
  }
}

export function usePopOut(box: HTMLElement, restore: () => void) {
  const [supported] = useState(popOutSupported)
  const [active, setActive] = useState(false)
  const [ctl] = useState(() => createPopOut(box, restore, setActive))
  useEffect(() => {
    ctl.setRestore(restore)
  }, [ctl, restore])
  // Leaving the page puts the element back before React removes it.
  useEffect(() => () => ctl.dispose(), [ctl])
  return { supported, active, open: ctl.open, close: ctl.close }
}
