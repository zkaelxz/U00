// Page jumps for the two readers (Comic, SavedMangaReader): bring a requested
// page into view once it is rendered, and in vertical mode follow the page
// crossing the middle of the screen.
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { useLatest } from '../../hooks/useLatest'

interface Jump {
  page: number
  seq: number
}

// Split from usePageJump because the readers' go() needs requestJump while the
// observer needs go(); one hook would reference go before it is declared.
export function useJumpRequest(start: number | null) {
  // seq makes a repeat request for the same page new.
  const [jump, setJump] = useState<Jump | null>(null)
  const activeJump = useMemo(() => jump ?? (start !== null ? { page: start, seq: 0 } : null), [jump, start])
  const requestJump = useCallback((page: number) => setJump((j) => ({ page, seq: (j?.seq ?? 0) + 1 })), [])
  return { activeJump, requestJump }
}

interface Options {
  activeJump: Jump | null
  current: number | null
  vertical: boolean
  // Re-run the scroll when the page list changes.
  pages: readonly unknown[]
  figures: RefObject<Map<number, HTMLElement>>
  stageRef: RefObject<HTMLElement | null>
  // Called with the page crossing the middle while scrolling.
  onVisible: (page: number) => void
  // Lets a reader ignore intersections that are only layout (before the first scroll).
  canObserve?: () => boolean
}

export function usePageJump({ activeJump, current, vertical, pages, figures, stageRef, onVisible, canObserve }: Options) {
  // The last jump request brought into view.
  const jumpHandled = useRef(-1)
  const jumpRef = useLatest(activeJump)
  // Latest callback without re-subscribing the observer; the readers define their go() after calling this hook.
  const onVisibleRef = useLatest(onVisible)

  useEffect(() => {
    if (!activeJump || jumpHandled.current === activeJump.seq || current !== activeJump.page) return
    const el = vertical ? figures.current?.get(activeJump.page) : stageRef.current
    if (!el) return
    jumpHandled.current = activeJump.seq
    const top = el.getBoundingClientRect().top
    if (top < 0 || top > window.innerHeight / 2) el.scrollIntoView({ block: 'start' })
  }, [activeJump, current, vertical, pages, figures, stageRef])

  const ready = current !== null
  useEffect(() => {
    if (!vertical || pages.length === 0 || !ready) return
    if (typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      (entries) => {
        const j = jumpRef.current
        // The observer would otherwise pull the page back before the jump lands.
        if (j && jumpHandled.current !== j.seq) return
        if (canObserve && !canObserve()) return
        for (const e of entries) {
          if (!e.isIntersecting) continue
          const n = Number((e.target as HTMLElement).dataset.page)
          if (n) onVisibleRef.current(n)
        }
      },
      { rootMargin: '-50% 0px -50% 0px', threshold: 0 },
    )
    for (const el of figures.current?.values() ?? []) io.observe(el)
    return () => io.disconnect()
  }, [vertical, pages, ready, canObserve, figures])

}
