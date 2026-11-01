import { useEffect, useState, type RefObject } from 'react'

const COLLAPSE_PX = 160
const COLLAPSE_BACK_PX = 20

// Phones: once the page is scrolled the sticky toolbar shrinks to its chip row so more lines show.
// Collapsing also hides the app header, stage tabs and player above the lines, which shortens the page by about 110px and makes the
// browser pull scrollY down by that much to keep the lines still. So the expand threshold must sit below COLLAPSE_PX minus that
// shift (and above zero); with a gap narrower than the shift the bar collapses, lands under the expand line, expands, lands over the
// collapse line and flickers forever while the finger is still scrolling.
export function useScrollCollapse(isPhone: boolean): boolean {
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    if (!isPhone) return
    const onScroll = () => setScrolled((was) => (was ? window.scrollY > COLLAPSE_BACK_PX : window.scrollY > COLLAPSE_PX))
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [isPhone])
  return isPhone && scrolled
}

// Rows keep clear of the sticky toolbar through scroll-margin-top, which follows its measured height.
// Runs after every render because the toolbar element is replaced when the empty-drama state flips.
export function useToolbarHeight(sectionRef: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const section = sectionRef.current
    const bar = section?.querySelector<HTMLElement>('.review-toolbar')
    if (!section || !bar || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => section.style.setProperty('--review-toolbar-h', `${bar.offsetHeight}px`))
    ro.observe(bar)
    return () => ro.disconnect()
  })
}

// Clears a transient value (a status line, a highlight) a while after it was set.
export function useClearAfter<T>(value: T | null, clear: () => void, ms: number) {
  useEffect(() => {
    if (value === null || value === '') return
    const t = setTimeout(clear, ms)
    return () => clearTimeout(t)
    // `clear` only calls a state setter, so a new function each render must not restart the timer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, ms])
}
