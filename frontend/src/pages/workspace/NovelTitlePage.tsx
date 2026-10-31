import { useEffect, useState } from 'react'

import { getRawChapters } from '../../api/novelChapters'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { routeHref } from '../../router'
import type { NovelChapterList } from '../../types/novelChapters'
import { chaptersHeadline, rowMeta, rowTitle } from './stages/novelChapters'
import './stages/novelChapters.css'

const PAGE = 100

// A novel is text that already exists, so its title page skips the video
// stepper: one chapter list and the four things done to it. The stage routes
// stay reachable by link for what this page leaves out.
export function NovelTitlePage({ id, drama }: { id: number; drama: DramaDetail }) {
  const [list, setList] = useState<NovelChapterList | null>(null)
  const [more, setMore] = useState(0)
  const [error, setError] = useState<unknown>(null)
  const title = drama.title_en || drama.title_zh || `Title #${id}`

  useEffect(() => {
    let cancelled = false
    getRawChapters(id, 0, PAGE * (more + 1)).then(
      (l) => !cancelled && setList(l),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [id, more])

  const rows = list?.chapters ?? []
  const marks = !!list && list.translation_chars > 0
  const stage = (s: string, tab?: 'glossary') => routeHref({ name: 'drama', id, stage: s, ...(tab ? { tab } : {}) })

  return (
    <section className="workspace novel-title" aria-label={`Title ${id} workspace`}>
      <header className="workspace-header">
        <ButtonLink href={routeHref({ name: 'library' })} variant="ghost" size="sm" className="ws-back" aria-label="Back to Library">
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">Library</span>
        </ButtonLink>
        <h1 data-testid="drama-title" title={title}>{title}</h1>
        <div className="ws-badges">
          <Badge kind="mediaType" value={drama.media_type} />
        </div>
      </header>
      <div className="actions">
        <ButtonLink href={stage('translate')} variant="primary">Translate</ButtonLink>
        <ButtonLink href={stage('source')}>Add chapters</ButtonLink>
        <ButtonLink href={stage('translate', 'glossary')}>Glossary</ButtonLink>
        <ButtonLink href={stage('export')}>Export</ButtonLink>
        <ButtonLink href={routeHref({ name: 'read', id, page: null })}>Read</ButtonLink>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <section className="panel" aria-label="Saved chapters">
        <h2 className="source-subhead">Saved chapters</h2>
        <p className="muted" data-testid="chapters-headline">{chaptersHeadline(list)}</p>
        {list && rows.length > 0 && (
          <ul className="chapters-list" aria-label="Saved chapters">
            {rows.map((r) => (
              <li key={r.number} className="chapters-item">
                <a className="chapters-row" href={stage('source')}>
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
                </a>
              </li>
            ))}
          </ul>
        )}
        {list && rows.length < list.total && (
          <div className="actions">
            <button type="button" onClick={() => setMore((n) => n + 1)}>Show more chapters</button>
            <span className="muted">{rows.length.toLocaleString()} of {list.total.toLocaleString()}</span>
          </div>
        )}
      </section>
    </section>
  )
}
