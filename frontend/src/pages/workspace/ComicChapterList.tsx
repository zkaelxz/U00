import { useEffect, useState } from 'react'

import { comicApi } from '../../api/comic'
import { routeHref } from '../../router'
import { chapterRows, type ChapterRow } from '../comic/chapterLogic'

/** The comic's own chapters on its Simple page; each opens the reader on that chapter's first page. */
// `refresh` changes when a job ends, so pages the job added show up without a reload.
export function ComicChapterList({ id, refresh = 0 }: { id: number; refresh?: number }) {
  const [rows, setRows] = useState<ChapterRow[]>([])

  useEffect(() => {
    let cancelled = false
    // A failed read leaves the list out; Open in Scanlate above still reaches every page.
    comicApi.pages(id).then((r) => !cancelled && setRows(chapterRows(r.pages, r.chapters)), () => undefined)
    return () => {
      cancelled = true
    }
  }, [id, refresh])

  if (rows.length === 0) return null
  return (
    <section className="panel" aria-label="Chapters">
      <h2 className="source-subhead">Chapters</h2>
      <ul className="chapters-list episodes-list" aria-label="Chapters">
        {rows.map((r) => (
          <li key={r.id} className="chapters-item">
            <a className="chapters-row episodes-row" href={routeHref({ name: 'comic', id, page: r.page })}>
              <span className="chapters-title">{r.label}</span>
              <span className="chapters-meta muted">{r.meta}</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}
