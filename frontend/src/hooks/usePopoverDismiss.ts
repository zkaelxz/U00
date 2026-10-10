import { useEffect, type RefObject } from 'react'

// Desktop popover dismissal: Esc (focus back to the trigger) and a pointer
// outside close it. `closeOnWindowBlur` also closes when the window loses
// focus: a click into an iframe never reaches this document but does blur it.
export function usePopoverDismiss(
  active: boolean,
  wrap: RefObject<HTMLElement | null>,
  trigger: RefObject<HTMLElement | null>,
  close: () => void,
  closeOnWindowBlur = false,
) {
  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      close()
      trigger.current?.focus()
    }
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) close()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    if (closeOnWindowBlur) window.addEventListener('blur', close)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onDown)
      if (closeOnWindowBlur) window.removeEventListener('blur', close)
    }
  }, [active, wrap, trigger, close, closeOnWindowBlur])
}
