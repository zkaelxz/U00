/*
 * One comic page: the image (sized from C1's width/height so nothing jumps
 * while it loads), the optional text boxes, and a plain message in place of
 * the image when it cannot load. Server text is only ever rendered as text.
 */
import { useEffect, useRef, useState, type CSSProperties, type Ref } from 'react'

import { probeImage, type ImageProblem } from '../../api/comic'
import type { ComicPageInfo, ComicRegionsResponse } from '../../types/comic'
import { orderedLines, regionBox, type ComicFit } from './comicLogic'
import { PageActions } from './PageActions'
import { longPress, MENU_AFTER_PEEK_MS } from './pageActionsLogic'

export const FORBIDDEN_TEXT = 'Needs media playback permission — ask the owner.'

const PROBLEM_TEXT: Record<ImageProblem, string> = {
  forbidden: FORBIDDEN_TEXT,
  missing: "This page's image is missing.",
  failed: "This page's image could not be loaded.",
}

type Props = {
  page: ComicPageInfo
  number: number
  count: number
  src: string
  fit: ComicFit
  eager: boolean
  // False: only the sized box is drawn, so the browser can free the decoded image.
  mounted?: boolean
  // Text boxes to draw (null: text off, or not loaded yet).
  regions: ComicRegionsResponse | null
  // Phone: numbered boxes only (the lines are in the sheet). Desktop: the text too.
  boxText: boolean
  // Set when a tap on a text box should open it for editing.
  onLineTap?: (idx: number) => void
  // Box numbers to mark (from a warning that names them).
  flashBoxes?: number[]
  // Set where a held finger or a right-click on the picture opens the page menu (save, share, cover).
  menu?: { dramaId: number; title: string }
  // The original picture, set while the page shows its translation: holding a finger on the page shows it until release.
  peekSrc?: string
  onProblem: (problem: ImageProblem) => void
  figureRef?: Ref<HTMLElement>
}

export function ComicPageView({ page, number, count, src, fit, eager, mounted = true, regions, boxText, onLineTap, flashBoxes, menu, peekSrc, onProblem, figureRef }: Props) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [peeking, setPeeking] = useState(false)
  const touched = useRef(false)
  const menuTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const endPress = () => {
    clearTimeout(menuTimer.current)
    setPeeking(false)
  }
  // A phone has no right-click, so holding on after the peek is still how its page menu opens.
  const onLongPress = menu
    ? peekSrc
      ? () => {
          setPeeking(true)
          menuTimer.current = setTimeout(() => {
            setPeeking(false)
            setMenuOpen(true)
          }, MENU_AFTER_PEEK_MS)
        }
      : () => setMenuOpen(true)
    : undefined
  const shownSrc = peeking && peekSrc ? peekSrc : src
  const [press] = useState(() => longPress())
  useEffect(() => () => {
    press.cancel()
    clearTimeout(menuTimer.current)
  }, [press])
  const [problem, setProblem] = useState<{ src: string; kind: ImageProblem } | null>(null)
  const shownProblem = problem && problem.src === shownSrc ? problem.kind : null
  const w = page.width && page.width > 0 ? page.width : null
  const h = page.height && page.height > 0 ? page.height : null

  useEffect(() => {
    if (shownProblem) onProblem(shownProblem)
  }, [shownProblem, onProblem])

  // A page without stored dimensions is a placeholder until its image loads.
  // Browsers anchor the scroll differently (the stylesheet turns native
  // anchoring off), so when one above the reader changes size, scroll by the
  // difference to keep what they are looking at in place.
  const innerRef = useRef<HTMLDivElement>(null)
  const sized = !!(w && h)
  // An unsized page keeps its loaded height while its image is released, so
  // the pages below don't move.
  const [measured, setMeasured] = useState<number | null>(null)
  useEffect(() => {
    const el = innerRef.current
    if (sized || !el || typeof ResizeObserver === 'undefined') return
    let last = el.getBoundingClientRect().height
    const ro = new ResizeObserver(() => {
      const box = el.getBoundingClientRect()
      const delta = box.height - last
      last = box.height
      setMeasured(box.height)
      if (delta !== 0 && box.bottom - delta <= 0) window.scrollBy(0, delta)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [sized, src])

  const onError = () => {
    probeImage(shownSrc).then((kind) => setProblem({ src: shownSrc, kind }))
  }

  const inner: CSSProperties = {}
  if (!mounted && !sized && measured) inner.minHeight = `${measured}px`
  if (w && h) {
    inner.aspectRatio = `${w} / ${h}`
    if (fit === 'height') inner.width = `min(100%, calc(var(--comic-avail-h) * ${w / h}))`
    if (fit === 'original') inner.width = `${w}px`
  }

  // Boxes are in the original image's pixels; the typeset image has the same size.
  const bw = regions?.width ?? w
  const bh = regions?.height ?? h
  const lines = regions ? orderedLines(regions.regions) : []
  const byIdx = new Map((regions?.regions ?? []).map((r) => [r.idx, r]))

  return (
    <figure
      ref={figureRef}
      className={`comic-page comic-fit-${fit}`}
      data-page={number}
      data-testid="comic-page"
      aria-label={`Page ${number} of ${count}`}
    >
      <div ref={innerRef} className={shownProblem ? 'comic-page-inner comic-page-broken' : 'comic-page-inner'} style={inner}>
        {shownProblem ? (
          <p className="comic-page-problem" role="note">
            <span>Page {number}</span>
            <span>{PROBLEM_TEXT[shownProblem]}</span>
          </p>
        ) : mounted ? (
          <img
            src={shownSrc}
            alt={`Page ${number}`}
            width={w ?? undefined}
            height={h ?? undefined}
            loading={eager ? 'eager' : 'lazy'}
            decoding="async"
            draggable={false}
            onError={onError}
            // Touch only: a mouse has the right-click, and holding its button is how a zoomed page is dragged.
            onPointerDown={onLongPress ? (e) => {
              touched.current = e.pointerType !== 'mouse'
              if (touched.current) press.down({ x: e.clientX, y: e.clientY }, e.isPrimary ? 1 : 2, onLongPress)
            } : undefined}
            onPointerMove={onLongPress ? (e) => press.move({ x: e.clientX, y: e.clientY }) : undefined}
            onPointerUp={() => { press.cancel(); endPress() }}
            onPointerCancel={() => { press.cancel(); endPress() }}
            onPointerLeave={() => { press.cancel(); endPress() }}
            onContextMenu={onLongPress ? (e) => {
              e.preventDefault()
              // The browser's own touch long-press also fires this; the hold timers above already handle touch.
              if (peekSrc && touched.current) return
              press.cancel()
              setMenuOpen(true)
            } : undefined}
          />
        ) : null}
        {!shownProblem && mounted && lines.length > 0 && (
          <div className="comic-boxes" data-testid="comic-boxes">
            {lines.map((l, i) => {
              const r = byIdx.get(l.idx)
              const box = r ? regionBox(r, bw, bh) : null
              if (!box) return null
              return (
                <div
                  key={l.idx}
                  className={boxText ? 'comic-box comic-box-text' : 'comic-box'}
                  data-tappable={onLineTap ? true : undefined}
                  data-flash={flashBoxes?.includes(i + 1) ? true : undefined}
                  onClick={onLineTap ? () => onLineTap(l.idx) : undefined}
                  style={{ left: `${box.left}%`, top: `${box.top}%`, width: `${box.width}%`, height: `${box.height}%` }}
                >
                  <span className="comic-box-num">{i + 1}</span>
                  {boxText && <span className="comic-box-line">{l.text}</span>}
                </div>
              )
            })}
          </div>
        )}
      </div>
      {/* Mounted only while open: a long strip has hundreds of pages and each sheet is a dialog. */}
      {menu && menuOpen && (
        // The stage captures the pointer on a press (to drag a zoomed page), which would take the sheet's clicks.
        <div onPointerDown={(e) => e.stopPropagation()}>
          <PageActions dramaId={menu.dramaId} title={menu.title} pick={{ number, src }} onClose={() => setMenuOpen(false)} />
        </div>
      )}
    </figure>
  )
}
