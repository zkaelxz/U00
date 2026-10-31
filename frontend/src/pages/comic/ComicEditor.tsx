/*
 * Comic text-box editor (#/comic/<id>/edit[?page=N]). Select a box on the page
 * to move it, resize it by a corner, or change its English, font, kind, size
 * and colour in the side panel; a drawn box starts as a bubble. A drop or
 * Apply is one PATCH carrying the page rev from the last response
 * (editorState.ts), so each is one undo step. Save page
 * re-renders the typeset image with the existing render job.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { comicApi, comicImageUrl } from '../../api/comic'
import { scanlateApi } from '../../api/scanlate'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { useJob } from '../../hooks/useJob'
import { routeHref } from '../../router'
import type { ComicPagesResponse } from '../../types/comic'
import { isActiveStatus } from '../../types/jobs'
import { MAX_REGION_TEXT_CHARS, type ScanlateRegion } from '../../types/scanlate'
import { chapterLabel } from './chapterLogic'
import { drawnBox, moveBox, resizeBox, sameBox, screenToPage, type Box, type Corner } from './editorGeometry'
import { applyEdit, createBox, deleteBox, draftFields, draftOf, draftValid, undoEdit, type Draft, type EditorApi, type EditorState } from './editorState'
import './comicEditor.css'

// Values the server's font_category accepts (scanlate_render.FONT_CATEGORIES); shown as stored.
const FONTS = ['regular', 'bold', 'handwritten']
// Values the server's kind accepts (scanlate_regions.REGION_KINDS); shown as stored.
const KINDS = ['bubble', 'thought', 'narration', 'caption', 'sign', 'sfx']
const SWATCHES = ['#111111', '#ffffff', '#b3261e']
const CORNERS: Corner[] = ['nw', 'ne', 'sw', 'se']

interface Drag {
  regionId: number
  mode: 'move' | Corner
  startX: number
  startY: number
  start: Box
  scale: number
}

const boxOf = (r: ScanlateRegion): Box => ({ x: r.x, y: r.y, w: r.w, h: r.h })

export default function ComicEditor({ id, page: routePage }: { id: number; page: number | null }) {
  const [pages, setPages] = useState<ComicPagesResponse | null>(null)
  const [loadError, setLoadError] = useState<unknown>(null)
  const [ed, setEd] = useState<EditorState>({ page: null, error: null })
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [draftState, setDraftState] = useState<{ key: string; value: Draft } | null>(null)
  const [live, setLive] = useState<{ regionId: number; box: Box } | null>(null)
  const [busy, setBusy] = useState(false)
  const [showOriginal, setShowOriginal] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobError, setJobError] = useState<unknown>(null)
  const [tool, setTool] = useState<'select' | 'draw'>('select')
  const [drawing, setDrawing] = useState<Box | null>(null)
  const drawFrom = useRef<{ x: number; y: number } | null>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const inFlight = useRef(false)

  const loadPages = useCallback(() => {
    comicApi.pages(id).then(
      (d) => {
        setLoadError(null)
        setPages(d)
      },
      setLoadError,
    )
  }, [id])
  useEffect(loadPages, [loadPages])

  const count = pages?.pages.length ?? 0
  const ordinal = pages ? Math.min(Math.max(routePage ?? 1, 1), Math.max(count, 1)) : 1
  const info = pages?.pages[ordinal - 1]
  const pageId = info?.id ?? null

  const api = useMemo<EditorApi | null>(
    () =>
      pageId === null
        ? null
        : {
            page: () => scanlateApi.page(id, pageId),
            editRegion: (rid, edit) => scanlateApi.editRegion(id, pageId, rid, edit),
            createRegion: (box, rev) => scanlateApi.createRegion(id, pageId, { expected_rev: rev, region: box }),
            deleteRegion: (rid, rev) => scanlateApi.deleteRegion(id, pageId, rid, rev),
            undo: (rev) => scanlateApi.undo(id, pageId, rev),
          },
    [id, pageId],
  )

  useEffect(() => {
    if (!api) return
    let cancelled = false
    api.page().then(
      (p) => {
        if (!cancelled) setEd({ page: p, error: null })
      },
      (e) => {
        if (!cancelled) setEd({ page: null, error: e })
      },
    )
    return () => {
      cancelled = true
    }
  }, [api])

  // A page the editor left holds no selection or drag.
  const [seenPage, setSeenPage] = useState(pageId)
  if (seenPage !== pageId) {
    setSeenPage(pageId)
    setEd({ page: null, error: null })
    setSelectedId(null)
    setLive(null)
    setDrawing(null)
  }

  const detail = ed.page
  const regions = useMemo(() => [...(detail?.regions ?? [])].sort((a, b) => a.idx - b.idx), [detail])
  const selected = regions.find((r) => r.id === selectedId) ?? null
  const draftKey = selected && detail ? `${selected.id}:${detail.rev}` : ''
  const draft = selected ? (draftState?.key === draftKey ? draftState.value : draftOf(selected)) : null
  const setDraft = (patch: Partial<Draft>) => {
    if (selected && draft) setDraftState({ key: draftKey, value: { ...draft, ...patch } })
  }

  const run = async (op: (state: EditorState, api: EditorApi) => Promise<EditorState>): Promise<EditorState | null> => {
    if (!api || inFlight.current) return null
    inFlight.current = true
    setBusy(true)
    try {
      const next = await op(ed, api)
      setEd(next)
      return next
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  const job = useJob(jobId, {
    onDone: (j) => {
      if (j.status === 'done') loadPages()
      else setJobError(new Error(j.error ?? j.message))
      setJobId(null)
    },
  })
  const rendering = isActiveStatus(job.status) || jobId !== null

  const save = async () => {
    if (pageId === null) return
    setJobError(null)
    try {
      setJobId((await scanlateApi.render(id, pageId)).job_id)
    } catch (e) {
      setJobError(e)
    }
  }

  const go = (n: number) => window.location.replace(routeHref({ name: 'comic-edit', id, page: n }))

  const beginDrag = (e: React.PointerEvent, r: ScanlateRegion, mode: Drag['mode']) => {
    const rect = stageRef.current?.getBoundingClientRect()
    if (!rect || !detail || busy) return
    e.stopPropagation()
    e.currentTarget.setPointerCapture(e.pointerId)
    setSelectedId(r.id)
    drag.current = { regionId: r.id, mode, startX: e.clientX, startY: e.clientY, start: boxOf(r), scale: rect.width / detail.width }
  }
  const dragged = (e: React.PointerEvent): Box | null => {
    const d = drag.current
    if (!d || !detail) return null
    const dx = screenToPage(e.clientX - d.startX, d.scale)
    const dy = screenToPage(e.clientY - d.startY, d.scale)
    return d.mode === 'move' ? moveBox(d.start, dx, dy, detail) : resizeBox(d.start, d.mode, dx, dy, detail)
  }
  // A handle's events bubble to its box, which must not handle the same drag twice.
  const moveDrag = (e: React.PointerEvent) => {
    e.stopPropagation()
    const box = dragged(e)
    if (box && drag.current) setLive({ regionId: drag.current.regionId, box })
  }
  const endDrag = async (e: React.PointerEvent) => {
    e.stopPropagation()
    const d = drag.current
    const box = dragged(e)
    drag.current = null
    if (!d || !box || sameBox(box, d.start)) {
      setLive(null)
      return
    }
    // The box stays where it was dropped until the server answers.
    setLive({ regionId: d.regionId, box })
    await run((state, a) => applyEdit(state, a, d.regionId, box))
    setLive(null)
  }

  const pagePoint = (e: React.PointerEvent) => {
    const rect = stageRef.current?.getBoundingClientRect()
    if (!rect || !detail) return null
    const scale = rect.width / detail.width
    return { x: screenToPage(e.clientX - rect.left, scale), y: screenToPage(e.clientY - rect.top, scale) }
  }
  const beginDraw = (e: React.PointerEvent) => {
    const p = pagePoint(e)
    if (!p || busy) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drawFrom.current = p
  }
  const moveDraw = (e: React.PointerEvent) => {
    const p = pagePoint(e)
    if (drawFrom.current && p && detail) setDrawing(drawnBox(drawFrom.current, p, detail))
  }
  const cancelDraw = () => {
    drawFrom.current = null
    setDrawing(null)
  }
  const endDraw = async (e: React.PointerEvent) => {
    const from = drawFrom.current
    const p = pagePoint(e)
    cancelDraw()
    if (!from || !p || !detail) return
    const box = drawnBox(from, p, detail)
    if (!box) return
    setTool('select')
    const before = detail.regions.length
    const next = await run((state, a) => createBox(state, a, box))
    const added = next?.page
    if (next && !next.error && added && added.regions.length > before) setSelectedId(added.regions[added.regions.length - 1].id)
  }
  const remove = async (regionId: number) => {
    const next = await run((state, a) => deleteBox(state, a, regionId))
    if (next && !next.error) setSelectedId(null)
  }

  const shownBox = (r: ScanlateRegion): Box => (live?.regionId === r.id ? live.box : boxOf(r))
  const variant = showOriginal || !info?.has_rendered ? 'original' : 'rendered'
  const chapter = info?.chapter_id ? pages?.chapters.find((c) => c.id === info.chapter_id) : undefined
  const heading = pages
    ? chapter?.known
      ? `${chapterLabel(chapter, pages.chapters.indexOf(chapter))} · Page ${info?.chapter_page ?? ordinal} of ${chapter.page_count}`
      : `Page ${ordinal} of ${count}`
    : ''
  const error = ed.error ?? loadError ?? jobError

  return (
    <div className="ce">
      <header className="ce-top">
        <h1 className="ce-title">{heading}</h1>
        <div className="ce-actions">
          <button type="button" className={buttonClass()} disabled={busy || !detail || detail.undo_depth === 0} onClick={() => run(undoEdit)}>
            Undo
          </button>
          <button type="button" className={buttonClass()} aria-pressed={showOriginal} onClick={() => setShowOriginal((v) => !v)}>
            Show Original
          </button>
          <button type="button" className={buttonClass()} disabled={ordinal <= 1} onClick={() => go(ordinal - 1)}>
            Previous
          </button>
          <button type="button" className={buttonClass()} disabled={ordinal >= count} onClick={() => go(ordinal + 1)}>
            Next
          </button>
          <button type="button" className={buttonClass('primary')} disabled={rendering || busy || pageId === null} onClick={save}>
            Save page
          </button>
        </div>
      </header>
      <ErrorBanner
        error={error}
        onDismiss={() => {
          setEd((s) => ({ ...s, error: null }))
          setLoadError(null)
          setJobError(null)
        }}
      />
      <div className="ce-body">
        <div className="ce-canvas">
          <div className="ce-tools">
            <button type="button" className={buttonClass('secondary', 'sm')} aria-pressed={tool === 'select'} onClick={() => setTool('select')}>
              Select
            </button>
            <button type="button" className={buttonClass('secondary', 'sm')} aria-pressed={tool === 'draw'} onClick={() => setTool('draw')}>
              Draw
            </button>
          </div>
          {info && detail && (
            <div
              ref={stageRef}
              className="ce-stage"
              data-tool={tool}
              data-testid="ce-stage"
              style={{ aspectRatio: `${detail.width} / ${detail.height}`, width: `min(100%, calc(72vh * ${detail.width} / ${detail.height}))` }}
              onPointerDown={(e) => (tool === 'draw' ? beginDraw(e) : setSelectedId(null))}
              onPointerMove={tool === 'draw' ? moveDraw : undefined}
              onPointerUp={tool === 'draw' ? endDraw : undefined}
              onPointerCancel={tool === 'draw' ? cancelDraw : undefined}
            >
              <img
                className="ce-img"
                alt=""
                draggable={false}
                src={comicImageUrl(id, info.id, variant, info.image_version)}
              />
              {regions.map((r, i) => {
                const b = shownBox(r)
                const on = r.id === selectedId
                return (
                  <div
                    key={r.id}
                    className="ce-box"
                    data-testid={`ce-box-${r.id}`}
                    data-selected={on || undefined}
                    role="button"
                    tabIndex={0}
                    aria-label={`Text box ${i + 1}`}
                    aria-pressed={on}
                    style={{
                      left: `${(b.x / detail.width) * 100}%`,
                      top: `${(b.y / detail.height) * 100}%`,
                      width: `${(b.w / detail.width) * 100}%`,
                      height: `${(b.h / detail.height) * 100}%`,
                    }}
                    onPointerDown={(e) => beginDrag(e, r, 'move')}
                    onPointerMove={moveDrag}
                    onPointerUp={endDrag}
                    onPointerCancel={() => {
                      drag.current = null
                      setLive(null)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setSelectedId(r.id)
                      }
                    }}
                  >
                    {on &&
                      CORNERS.map((c) => (
                        <span
                          key={c}
                          className={`ce-handle ce-handle-${c}`}
                          data-testid={`ce-handle-${c}`}
                          onPointerDown={(e) => beginDrag(e, r, c)}
                          onPointerMove={moveDrag}
                          onPointerUp={endDrag}
                          onPointerCancel={() => {
                            drag.current = null
                            setLive(null)
                          }}
                        />
                      ))}
                  </div>
                )
              })}
              {drawing && (
                <div
                  className="ce-drawing"
                  data-testid="ce-drawing"
                  style={{
                    left: `${(drawing.x / detail.width) * 100}%`,
                    top: `${(drawing.y / detail.height) * 100}%`,
                    width: `${(drawing.w / detail.width) * 100}%`,
                    height: `${(drawing.h / detail.height) * 100}%`,
                  }}
                />
              )}
            </div>
          )}
        </div>
        {selected && draft && (
          <aside className="ce-panel" aria-label={`Text box ${regions.indexOf(selected) + 1}`}>
            <h2 className="ce-panel-title">{`Text box ${regions.indexOf(selected) + 1}`}</h2>
            <label className="ce-field">
              <span>English</span>
              <textarea
                rows={3}
                maxLength={MAX_REGION_TEXT_CHARS}
                value={draft.text}
                onChange={(e) => setDraft({ text: e.target.value })}
              />
            </label>
            <label className="ce-field">
              <span>Original</span>
              <textarea rows={2} readOnly value={selected.source_text} />
            </label>
            <div className="ce-row">
              <label className="ce-field">
                <span>Font</span>
                <select value={draft.fontCategory} onChange={(e) => setDraft({ fontCategory: e.target.value })}>
                  {(FONTS.includes(draft.fontCategory) ? FONTS : [draft.fontCategory, ...FONTS]).map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
              </label>
              <label className="ce-field">
                <span>Kind</span>
                <select value={draft.kind} onChange={(e) => setDraft({ kind: e.target.value })}>
                  {(KINDS.includes(draft.kind) ? KINDS : [draft.kind, ...KINDS]).map((k) => (
                    <option key={k} value={k}>
                      {k}
                    </option>
                  ))}
                </select>
              </label>
              <label className="ce-field ce-size">
                <span>Size</span>
                <input type="number" inputMode="numeric" min={6} max={200} value={draft.size} onChange={(e) => setDraft({ size: e.target.value })} />
              </label>
            </div>
            <div className="ce-field">
              <span id="ce-colour">Colour</span>
              <div className="ce-swatches" role="group" aria-labelledby="ce-colour">
                {SWATCHES.map((c) => (
                  <button
                    key={c}
                    type="button"
                    className="ce-swatch"
                    style={{ background: c }}
                    aria-label={c}
                    aria-pressed={draft.color?.toLowerCase() === c}
                    onClick={() => setDraft({ color: c })}
                  />
                ))}
              </div>
            </div>
            <div className="ce-row ce-apply">
              <button type="button" className={buttonClass()} disabled={busy} onClick={() => remove(selected.id)}>
                Delete box
              </button>
              <button
                type="button"
                className={buttonClass('primary')}
                disabled={busy || !draftValid(draft)}
                onClick={() => run((state, a) => applyEdit(state, a, selected.id, draftFields(draft)))}
              >
                Apply
              </button>
            </div>
          </aside>
        )}
      </div>
    </div>
  )
}
