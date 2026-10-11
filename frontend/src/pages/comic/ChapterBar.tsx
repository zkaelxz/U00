/*
 * The comic viewer's chapter row: a chapter selector with previous / next
 * chapter, "Page 3 of 24" within the chapter beside the overall position, the
 * "Show hidden (n)" toggle, and a Chapters & pages sheet (read only this
 * chapter, hide or restore pages that are not part of the story). Chapter
 * titles are the source's own and rendered as text only.
 */
import { useRef, useState } from 'react'

import { comicApi } from '../../api/comic'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Sheet } from '../../components/Sheet'
import { SourceLink } from '../discover/ExternalLink'
import type { ComicChapter, ComicPageInfo } from '../../types/comic'
import {
  chapterIndex,
  chapterLabel,
  chapterStart,
  optionText,
  positionOf,
  type ChapterPrefs,
} from './chapterLogic'

type Props = {
  dramaId: number
  pages: ComicPageInfo[]
  chapters: ComicChapter[]
  current: number
  visible: number[]
  prefs: ChapterPrefs
  hiddenCount: number
  onPrefs: (next: ChapterPrefs) => void
  onGo: (ordinal: number) => void
  // Pages were hidden or restored: reload the list.
  onChanged: () => void
}

export function ChapterBar({ dramaId, pages, chapters, current, visible, prefs, hiddenCount, onPrefs, onGo, onChanged }: Props) {
  // The page and chapter the sheet was opened on: opening a modal can shift the
  // layout and move the reading position, and "Hide" must act on what was seen.
  const [target, setTarget] = useState<{ page: ComicPageInfo; ordinal: number; chapter: ComicChapter | null; index: number } | null>(null)
  const open = target !== null
  // Closing the sheet returns focus to the "..." button, which is not sticky and so
  // scrolls the page back to the top; the reading position is put back after it.
  const openY = useRef(0)
  const closeSheet = () => {
    setTarget(null)
    const y = openY.current
    requestAnimationFrame(() => window.scrollTo(0, y))
  }
  const [edge, setEdge] = useState('1')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const page = pages[current - 1]
  const at = chapterIndex(chapters, page?.chapter_id)
  const chapter = at >= 0 ? chapters[at] : null
  const pos = positionOf(pages, current, visible)
  const multi = chapters.length > 1
  const n = Math.min(100, Math.max(1, Math.floor(Number(edge)) || 1))

  const jump = (i: number) => {
    const target = chapters[i]
    if (target) onGo(chapterStart(target, visible))
  }

  const hide = async (hidden: boolean, body: { page_ids?: number[]; chapter_id?: string; edge?: 'first' | 'last' | 'all'; count?: number }) => {
    setBusy(true)
    setError(null)
    try {
      await comicApi.setVisibility(dramaId, { hidden, ...body })
      closeSheet()
      onChanged()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const sheetPage = target?.page
  const sheetChapter = target?.chapter ?? null
  const chapterHidden = sheetChapter?.hidden_count ?? 0
  return (
    <div className="comic-chapters" role="group" aria-label="Chapters" data-testid="comic-chapters">
      {multi && (
        <>
          <button type="button" aria-label="Previous chapter" disabled={at <= 0} onClick={() => jump(at - 1)}>‹</button>
          <select aria-label="Chapter" value={chapter?.id ?? ''} onChange={(e) => jump(chapterIndex(chapters, e.target.value))}>
            {chapters.map((c, i) => (
              <option key={c.id} value={c.id}>{optionText(c, i, prefs.showHidden)}</option>
            ))}
          </select>
          <button type="button" aria-label="Next chapter" disabled={at < 0 || at >= chapters.length - 1} onClick={() => jump(at + 1)}>›</button>
        </>
      )}
      {multi && (
        <span
          className="comic-chapter-pos"
          data-testid="comic-chapter-pos"
          aria-label={`Page ${pos.inChapter} of ${pos.chapterTotal} in this chapter, ${pos.overall} of ${pos.overallTotal} overall`}
        >
          Page {pos.inChapter} of {pos.chapterTotal} <span className="muted">({pos.overall}/{pos.overallTotal})</span>
        </span>
      )}
      <SourceLink href={chapter?.url}>Open chapter page</SourceLink>
      {hiddenCount > 0 && (
        <button type="button" aria-pressed={prefs.showHidden} onClick={() => onPrefs({ ...prefs, showHidden: !prefs.showHidden })}>
          {prefs.showHidden ? 'Hide' : 'Show'} hidden ({hiddenCount})
        </button>
      )}
      <button
        type="button"
        aria-label="Chapters and pages"
        aria-haspopup="dialog"
        onClick={() => {
          openY.current = window.scrollY
          setTarget({ page, ordinal: current, chapter, index: at })
        }}
      >
        ⋯
      </button>

      <Sheet open={open} title="Chapters & pages" onClose={closeSheet}>
        <div className="stack comic-chapter-tools">
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
          {multi && (
            <Field label="Read only this chapter" help="Stops at the end of the chapter instead of reading on into the next.">
              <input
                type="checkbox"
                checked={prefs.chapterOnly}
                onChange={(e) => onPrefs({ ...prefs, chapterOnly: e.target.checked })}
              />
            </Field>
          )}
          <p className="muted">
            Hide credit and promo pages that are not part of the story. They stay saved, are skipped when translating, and can be restored.
          </p>
          <div className="actions">
            {sheetPage?.hidden ? (
              <button type="button" disabled={busy} onClick={() => void hide(false, { page_ids: [sheetPage.id] })}>
                Restore page {target?.ordinal}
              </button>
            ) : (
              <button type="button" disabled={busy || !sheetPage} onClick={() => sheetPage && void hide(true, { page_ids: [sheetPage.id] })}>
                Hide page {target?.ordinal}
              </button>
            )}
          </div>
          {sheetChapter && (
            <>
              <Field label={`Pages at the start or end of ${chapterLabel(sheetChapter, target?.index ?? 0)}`}>
                <input type="number" min={1} max={100} inputMode="numeric" value={edge} onChange={(e) => setEdge(e.target.value)} />
              </Field>
              <div className="actions">
                <button type="button" disabled={busy} onClick={() => void hide(true, { chapter_id: sheetChapter.id, edge: 'first', count: n })}>
                  Hide first {n}
                </button>
                <button type="button" disabled={busy} onClick={() => void hide(true, { chapter_id: sheetChapter.id, edge: 'last', count: n })}>
                  Hide last {n}
                </button>
                <button type="button" disabled={busy || chapterHidden === 0} onClick={() => void hide(false, { chapter_id: sheetChapter.id, edge: 'all' })}>
                  Restore all in this chapter{chapterHidden ? ` (${chapterHidden})` : ''}
                </button>
              </div>
            </>
          )}
        </div>
      </Sheet>
    </div>
  )
}
