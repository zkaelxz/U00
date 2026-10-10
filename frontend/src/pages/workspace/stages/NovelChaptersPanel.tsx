/*
 * NovelChaptersPanel: the chapters saved in a novel title's raw source text
 * (api/routers/novel_files_routes.py, GET .../raw-novel/chapters), so the
 * owner can see what and how many chapters there are and read the raws
 * before translating. Read-only; saving and removing stay in the Raw source
 * novel section.
 *
 * Rows come a page at a time and a preview loads one chapter a slice at a
 * time ("Show all" fetches the rest), so a many-megabyte file never reaches
 * the browser in one response. Titles saved before chapter tracking show as
 * one "Unsplit text" block. The marks say which chapters are already in the
 * text used for translation.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { getRawChapterText, getRawChapters } from '../../../api/novelChapters'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import { SourceLink } from '../../discover/ExternalLink'
import { copyText } from '../../../components/clipboard'
import type { NovelChapterList, NovelChapterRow, NovelChapterText } from '../../../types/novelChapters'
import { useStage } from '../StageContext'
import { chaptersHeadline, chaptersSummary, loadedLabel, neighbour, optionLabel, rowMeta, rowTitle, translationLine } from './novelChapters'
import { useNovelFilesVersion } from './novelFileEvents'
import './novelChapters.css'

const PAGE = 100
const FIRST_SLICE = 20000
const SLICE = 50000
// "Show all" stops after this many slices and offers itself again, so one
// click on a huge chapter can't pull in the whole file.
const SHOW_ALL_SLICES = 10

interface Preview {
  number: number
  head: NovelChapterText
  text: string
  next: number | null
}

export function NovelChaptersPanel() {
  const { dramaId } = useStage()
  const filesVersion = useNovelFilesVersion()
  const [list, setList] = useState<NovelChapterList | null>(null)
  const [rows, setRows] = useState<NovelChapterRow[]>([])
  const [reloads, setReloads] = useState(0)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const requestId = useRef(0)

  useEffect(() => {
    let cancelled = false
    getRawChapters(dramaId, 0, PAGE).then(
      (l) => {
        if (cancelled) return
        setList(l)
        setRows(l.chapters)
        setError(null)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads, filesVersion])

  // A different file (replaced, removed) makes an open preview stale.
  useEffect(() => {
    requestId.current += 1
    setPreview(null)
    setNotice(null)
  }, [dramaId, reloads, filesVersion])

  const more = useCallback(() => {
    getRawChapters(dramaId, rows.length, PAGE).then(
      (l) => setRows((r) => [...r, ...l.chapters]),
      (e: unknown) => setError(e),
    )
  }, [dramaId, rows.length])

  const open = (number: number) => {
    const id = ++requestId.current
    setNotice(null)
    setLoading(true)
    getRawChapterText(dramaId, number, 0, FIRST_SLICE).then(
      (t) => {
        if (id !== requestId.current) return
        setLoading(false)
        setPreview({ number, head: t, text: t.text, next: t.next_offset })
      },
      (e: unknown) => {
        if (id !== requestId.current) return
        setLoading(false)
        setError(e)
      },
    )
  }

  const showAll = async () => {
    if (!preview || preview.next === null) return
    const id = requestId.current
    setLoading(true)
    let text = preview.text
    let next: number | null = preview.next
    try {
      for (let i = 0; i < SHOW_ALL_SLICES && next !== null; i++) {
        const t: NovelChapterText = await getRawChapterText(dramaId, preview.number, next, SLICE)
        if (id !== requestId.current) return
        text += t.text
        next = t.next_offset
      }
      setPreview({ ...preview, text, next })
    } catch (e) {
      if (id === requestId.current) setError(e)
    }
    if (id === requestId.current) setLoading(false)
  }

  const copy = async () => {
    if (!preview) return
    const ok = await copyText(preview.text)
    setNotice(
      !ok
        ? 'Could not copy. Select the text and copy it yourself.'
        : preview.next === null
          ? 'Copied.'
          : `Copied the ${preview.text.length.toLocaleString()} characters shown. Use Show all to copy the rest.`,
    )
  }

  const hasText = !!list && list.present && list.char_count > 0
  const translation = list ? translationLine(list) : ''
  const marks = !!list && list.translation_chars > 0
  const prev = preview && list ? neighbour(preview.number, list.total, -1) : null
  const following = preview && list ? neighbour(preview.number, list.total, 1) : null

  return (
    <section className="panel" aria-label="Saved chapters">
      <Section storageKey="source.novel.chapters" defaultOpen title="Saved chapters" summary={chaptersSummary(list)}>
        <p className="muted" data-testid="chapters-headline">{chaptersHeadline(list)}</p>
        {hasText && translation && <p className="muted" data-testid="chapters-translation">{translation}</p>}
        {hasText && !list.split && (
          <p className="muted">
            Chapter boundaries are not known for this text (saved before chapters were tracked). Chapters imported from now on are listed one by one.
          </p>
        )}
        {hasText && list.split && list.total > 1 && (
          <div className="chapters-nav" role="group" aria-label="Chapter selector">
            <button type="button" aria-label="Previous chapter" disabled={loading || prev === null} onClick={() => prev !== null && open(prev)}>‹</button>
            <select
              aria-label="Chapter"
              value={preview?.number ?? ''}
              onChange={(e) => e.target.value && open(Number(e.target.value))}
            >
              {!preview && <option value="">Choose a chapter</option>}
              {preview && !rows.some((r) => r.number === preview.number) && (
                <option value={preview.number}>{preview.number}. {preview.head.title || `Chapter ${preview.number}`}</option>
              )}
              {rows.map((r) => (
                <option key={r.number} value={r.number}>{optionLabel(r)}</option>
              ))}
            </select>
            <button type="button" aria-label="Next chapter" disabled={loading || following === null} onClick={() => following !== null && open(following)}>›</button>
          </div>
        )}
        {hasText && (
          <ul className="chapters-list" aria-label="Saved chapters">
            {rows.map((r) => (
              <li key={r.number}>
                <button
                  type="button"
                  className="chapters-row"
                  aria-pressed={preview?.number === r.number}
                  onClick={() => open(r.number)}
                >
                  <span className="chapters-num">{r.unsplit && !r.title ? '' : r.number}</span>
                  <span className="chapters-title">{rowTitle(r)}</span>
                  <span className="chapters-meta muted">
                    {r.chars.toLocaleString()} chars{rowMeta(r) ? ` · ${rowMeta(r)}` : ''}
                  </span>
                  {marks && (
                    <span className={`chapters-mark${r.in_translation ? ' chapters-mark-in' : ''}`}>
                      {r.in_translation ? 'In translation text' : 'Not in translation text'}
                    </span>
                  )}
                </button>
                <SourceLink href={r.url}>Open chapter page</SourceLink>
              </li>
            ))}
          </ul>
        )}
        {list && rows.length < list.total && (
          <div className="actions">
            <button type="button" onClick={more}>Show more chapters</button>
            <span className="muted">{rows.length.toLocaleString()} of {list.total.toLocaleString()}</span>
          </div>
        )}
        {preview && (
          <div className="chapters-preview" role="region" aria-label="Chapter preview">
            <h4 className="source-subhead">{preview.head.title || `Chapter ${preview.number}`}</h4>
            <p className="muted">
              <SourceLink href={preview.head.url}>Open chapter page</SourceLink>{preview.head.url ? ' · ' : ''}
              {loadedLabel(preview.text.length, preview.head.chars)}
              {preview.head.in_translation ? ' · in the translation text' : ''}
            </p>
            <div className="chapters-text" tabIndex={0} data-testid="chapters-text">{preview.text}</div>
            <div className="actions">
              <button type="button" onClick={copy}>Copy</button>
              {preview.next !== null && (
                <button type="button" disabled={loading} onClick={showAll}>Show all</button>
              )}
              <button type="button" onClick={() => setPreview(null)}>Close</button>
            </div>
          </div>
        )}
        {loading && !preview && <p className="muted" role="status">Loading…</p>}
        {notice && <p role="status">{notice}</p>}
        <div className="actions">
          <button type="button" onClick={() => setReloads((n) => n + 1)}>Refresh</button>
        </div>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </Section>
    </section>
  )
}
