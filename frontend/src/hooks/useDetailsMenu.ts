import { useEffect, useRef } from 'react'

/**
 * A <details> menu that closes on a click elsewhere or Escape (focus returns
 * to its summary), like a native menu. Attach the ref to the <details>.
 */
export function useDetailsMenu() {
  const ref = useRef<HTMLDetailsElement>(null)
  useEffect(() => {
    const close = (e: Event) => {
      const el = ref.current
      if (!el?.open) return
      if (e instanceof KeyboardEvent) {
        if (e.key !== 'Escape') return
        el.open = false
        el.querySelector('summary')?.focus()
      } else if (!el.contains(e.target as Node)) el.open = false
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', close)
    }
  }, [])
  return ref
}
