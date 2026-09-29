/*
 * One comic page: the image (sized from C1's width/height so nothing jumps
 * while it loads), the optional text boxes, and a plain message in place of
 * the image when it cannot load. Server text is only ever rendered as text.
 */
import { useEffect, useState, type CSSProperties, type Ref } from 'react'

import { probeImage, type ImageProblem } from '../../api/comic'
import type { ComicPageInfo, ComicRegionsResponse } from '../../types/comic'
import { orderedLines, regionBox, type ComicFit } from './comicLogic'

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
  // Text boxes to draw (null: text off, or not loaded yet).
  regions: ComicRegionsResponse | null
  // Phone: numbered boxes only (the lines are in the sheet). Desktop: the text too.
  boxText: boolean
  onProblem: (problem: ImageProblem) => void
  figureRef?: Ref<HTMLElement>
}

export function ComicPageView({ page, number, count, src, fit, eager, regions, boxText, onProblem, figureRef }: Props) {
  const [problem, setProblem] = useState<{ src: string; kind: ImageProblem } | null>(null)
  const shownProblem = problem && problem.src === src ? problem.kind : null
  const w = page.width && page.width > 0 ? page.width : null
  const h = page.height && page.height > 0 ? page.height : null

  useEffect(() => {
    if (shownProblem) onProblem(shownProblem)
  }, [shownProblem, onProblem])

  const onError = () => {
    probeImage(src).then((kind) => setProblem({ src, kind }))
  }

  const inner: CSSProperties = {}
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
      <div className={shownProblem ? 'comic-page-inner comic-page-broken' : 'comic-page-inner'} style={inner}>
        {shownProblem ? (
          <p className="comic-page-problem" role="note">
            <span>Page {number}</span>
            <span>{PROBLEM_TEXT[shownProblem]}</span>
          </p>
        ) : (
          <img
            src={src}
            alt={`Page ${number}`}
            width={w ?? undefined}
            height={h ?? undefined}
            loading={eager ? 'eager' : 'lazy'}
            decoding="async"
            draggable={false}
            onError={onError}
          />
        )}
        {!shownProblem && lines.length > 0 && (
          <div className="comic-boxes" data-testid="comic-boxes">
            {lines.map((l, i) => {
              const r = byIdx.get(l.idx)
              const box = r ? regionBox(r, bw, bh) : null
              if (!box) return null
              return (
                <div
                  key={l.idx}
                  className={boxText ? 'comic-box comic-box-text' : 'comic-box'}
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
    </figure>
  )
}
