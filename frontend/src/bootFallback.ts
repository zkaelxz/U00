// Last-resort crash message. The React error boundaries catch render errors
// once the app is up; this covers what they can't: a crash before React
// mounts, or one that tears the whole tree down, which would otherwise
// leave an empty dark page. It only draws when #root holds nothing but the
// static "didn't load" note from index.html, so an unrelated error in a
// working app never replaces the page.

import { errorText } from './components/errorFallbackText'

export const BOOT_FALLBACK_TITLE = 'Baihe Studio could not start this page.'
// The static note in index.html's #root (shown if this script never runs).
export const BOOT_STATIC_ID = 'boot-static'

export function renderBootFallback(root: HTMLElement, error: unknown) {
  if (Array.from(root.children).some((c) => c.id !== BOOT_STATIC_ID)) return false
  const doc = root.ownerDocument
  const box = doc.createElement('div')
  box.className = 'boot-fallback'
  box.setAttribute('role', 'alert')
  const h = doc.createElement('h2')
  h.textContent = BOOT_FALLBACK_TITLE
  const p = doc.createElement('p')
  p.textContent = 'Try reloading. If it keeps happening, copy the message below into a bug report.'
  const pre = doc.createElement('pre')
  pre.className = 'error-fallback-text'
  pre.textContent = errorText(error)
  const btn = doc.createElement('button')
  btn.type = 'button'
  btn.className = 'primary'
  btn.textContent = 'Reload'
  btn.addEventListener('click', () => doc.defaultView?.location.reload())
  box.append(h, p, pre, btn)
  root.replaceChildren(box)
  return true
}

export function installBootFallback(root: HTMLElement, win: Window = window) {
  // Checked on the next tick: React reports an uncaught error while it is
  // still tearing the tree down, so #root is only empty afterwards.
  const later = (error: unknown) => win.setTimeout(() => renderBootFallback(root, error), 0)
  win.addEventListener('error', (e) => later(e.error ?? e.message))
  win.addEventListener('unhandledrejection', (e) => later(e.reason))
}
