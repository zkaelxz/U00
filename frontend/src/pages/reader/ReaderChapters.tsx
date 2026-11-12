/*
 * The Reader's chapter jump: a button beside the pager that opens the saved
 * chapters with a search box and how far each is translated, and lands on the
 * page that holds the chapter's first line.
 */
import { Fragment, useEffect, useMemo, useState } from 'react'

import { getLineChapters } from '../../api/novelChapters'
import { Sheet } from '../../components/Sheet'
import type { NovelLineChapterRow } from '../../types/novelChapters'
import { chapterAt, chapterPage } from './chapterPages'

const label = (c: NovelLineChapterRow) => (c.title ? `${c.number}. ${c.title}` : `Chapter ${c.number}`)

function stateOf(c: NovelLineChapterRow) {
  const done = c.line_count > 0 && c.translated >= c.line_count
  const base = done ? 'Translated' : c.translated > 0 ? `${c.translated} of ${c.line_count} translated` : 'Not translated'
  if (c.reviewed <= 0) return base
  return c.reviewed >= c.line_count ? `${base}, reviewed` : `${base}, ${c.reviewed} reviewed`
}

export function ChapterJump({ dramaId, page, pageSize, onGo }: {
  dramaId: number
  page: number
  pageSize: number
  onGo: (page: number) => void
}) {
  const [chapters, setChapters] = useState<NovelLineChapterRow[]>([])
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  useEffect(() => {
    let live = true
    getLineChapters(dramaId).then((r) => live && setChapters(r.chapters), () => {})
    return () => { live = false }
  }, [dramaId])
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? chapters.filter((c) => `${label(c)} ${c.title_en} ${c.part}`.toLowerCase().includes(q)) : chapters
  }, [chapters, query])
  // A title with no saved chapters has nothing to jump between.
  if (chapters.length < 2) return null
  const current = chapterAt(chapters, page, pageSize)
  return (
    <>
      <button type="button" className="reader-chapter-btn" aria-haspopup="dialog" onClick={() => setOpen(true)}>
        {current ? label(current) : 'Chapters'}
      </button>
      <Sheet open={open} title="Chapters" onClose={() => setOpen(false)}>
        <input type="search" aria-label="Search chapters" placeholder="Search chapters" value={query}
          onChange={(e) => setQuery(e.target.value)} />
        <ul className="reader-chapter-list">
          {shown.map((c, i) => (
            <Fragment key={c.number}>
              {c.part && c.part !== shown[i - 1]?.part && <li className="reader-chapter-part">{c.part}</li>}
              <li>
                <button type="button" aria-current={c === current ? 'true' : undefined}
                  onClick={() => { setOpen(false); onGo(chapterPage(c.first_idx, pageSize)) }}>
                  <span>{label(c)}{c.title_en && <span className="muted"> · {c.title_en}</span>}</span>
                  <span className="muted">{stateOf(c)}</span>
                </button>
              </li>
            </Fragment>
          ))}
          {shown.length === 0 && <li className="muted">No chapter matches.</li>}
        </ul>
      </Sheet>
    </>
  )
}
