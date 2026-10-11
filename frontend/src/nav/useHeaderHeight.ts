import { useCallback } from 'react'

// The header is rendered in two places (rail layout and phone layout), so a callback ref follows whichever is mounted.
export function useHeaderHeight() {
  return useCallback((el: HTMLElement | null) => {
    const root = document.documentElement
    if (!el) {
      root.style.removeProperty('--app-header-h')
      return
    }
    const apply = () => root.style.setProperty('--app-header-h', `${el.offsetHeight}px`)
    apply()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    // React 19 runs the returned cleanup instead of calling the ref with null.
    return () => { ro.disconnect(); root.style.removeProperty('--app-header-h') }
  }, [])
}
