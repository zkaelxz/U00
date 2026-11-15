// Owns publishing an element's live height as a CSS variable on the page.
import { useCallback, useRef } from 'react'

// The selection bar and the floating player sit just above this bar, which is taller in the check queue (two-line
// buttons) and while editing. Its live height goes on the page as a CSS variable they both read, and is gone while
// the bar is not shown.
export function usePublishedHeight(name: string) {
  const observer = useRef<ResizeObserver | null>(null)
  return useCallback((el: HTMLElement | null) => {
    const root = document.documentElement.style
    observer.current?.disconnect()
    observer.current = null
    if (!el) {
      root.removeProperty(name)
      return
    }
    const publish = () => root.setProperty(name, `${el.getBoundingClientRect().height}px`)
    publish()
    if (typeof ResizeObserver === 'undefined') return
    observer.current = new ResizeObserver(publish)
    observer.current.observe(el)
  }, [name])
}
