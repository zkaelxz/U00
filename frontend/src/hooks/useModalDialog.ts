import { useEffect, useRef, type KeyboardEvent } from 'react'

// Keeps a native <dialog> in step with React state. The attribute fallback is
// for engines without showModal (old WebViews), where the dialog still shows.
export function useModalDialog(open: boolean) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) {
      if (typeof d.showModal === 'function') d.showModal()
      else d.setAttribute('open', '')
    } else if (!open && d.open) {
      d.close()
    }
  }, [open])
  return ref
}

// A modal dialog lets Tab leave through the browser's own UI; wrap instead so
// focus cycles inside. Hidden controls (display:none beside a keyboard) are
// skipped so they never count as the last stop.
export function trapTabWithin(focusable: string) {
  return (e: KeyboardEvent<HTMLDialogElement>) => {
    if (e.key !== 'Tab') return
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>(focusable)].filter((el) => el.getClientRects().length > 0)
    const first = items.at(0)
    const last = items.at(-1)
    if (!first || !last) return
    const at = document.activeElement
    if (e.shiftKey && (at === first || at === e.currentTarget)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && at === last) {
      e.preventDefault()
      first.focus()
    }
  }
}
