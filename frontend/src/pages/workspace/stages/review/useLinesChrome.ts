import { useEffect, useLayoutEffect, useState, type RefObject } from 'react'

import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { TOOLS_BESIDE_LINES } from './reviewToolsTabs'

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

// Rows keep clear of the sticky toolbar through scroll-margin-top, which follows its measured height. The list card's top
// is published too, so the card can reach the window's bottom when its rows are few (review.css, 1400px and wider).
// Runs after every render because the toolbar element is replaced when the empty-drama state flips.
export function useToolbarHeight(sectionRef: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const section = sectionRef.current
    const bar = section?.querySelector<HTMLElement>('.review-toolbar')
    if (!section || !bar || typeof ResizeObserver === 'undefined') return
    const measure = () => {
      section.style.setProperty('--review-toolbar-h', `${bar.offsetHeight}px`)
      // A collapsed phone toolbar hides the header above the list, which moves the card up; the card's height is for the page as it opens, so that move is not measured.
      if (bar.classList.contains('is-collapsed')) return
      // A phone draws no box for the list's card (display: contents has no rectangle), so its first child, the list, marks the top.
      const wrapper = section.querySelector<HTMLElement>('.review-list-card')
      const card = wrapper && wrapper.getClientRects().length === 0 ? wrapper.firstElementChild : wrapper
      if (card) section.style.setProperty('--review-card-top', `${card.getBoundingClientRect().top + window.scrollY}px`)
    }
    const ro = new ResizeObserver(measure)
    ro.observe(bar)
    ro.observe(section)
    // The card's top moves with the title header's height (a job pill, a wrapped name, the queue's place) without the toolbar or the section changing size.
    const header = document.querySelector<HTMLElement>('.workspace-header')
    if (header) ro.observe(header)
    measure()
    return () => ro.disconnect()
  })
}

// Wide screens fit Review to the window, so the page itself never scrolls and only the lines and the side column do
// (review.css). The stage's height is the window less what sits above it, so its top is published as --review-top.
// Whatever above it changes height (a wrapped title, a job pill, a notice) moves that top, so every block above the
// stage, at each level up to the page, is watched, as is a block added or removed beside it.
export function useFitToScreen(ref: RefObject<HTMLElement | null>): boolean {
  const fits = useMediaQuery(TOOLS_BESIDE_LINES)
  // Before paint, so the first frame does not draw the stage at the full window's height.
  useLayoutEffect(() => {
    const stage = ref.current
    if (!fits || !stage) return
    const measure = () => stage.style.setProperty('--review-top', `${stage.getBoundingClientRect().top + window.scrollY}px`)
    const watch = () => {
      ro.disconnect()
      for (let el: Element | null = stage; el && el !== document.body; el = el.parentElement) {
        for (let prev = el.previousElementSibling; prev; prev = prev.previousElementSibling) ro.observe(prev)
      }
      measure()
    }
    const ro = new ResizeObserver(measure)
    const added = new MutationObserver(watch)
    if (stage.parentElement) added.observe(stage.parentElement, { childList: true })
    watch()
    window.addEventListener('resize', measure)
    return () => {
      ro.disconnect()
      added.disconnect()
      window.removeEventListener('resize', measure)
      stage.style.removeProperty('--review-top')
    }
  }, [fits, ref])
  return fits
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
