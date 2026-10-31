import { useEffect, useState } from 'react'

import { getRawChapters } from '../../api/novelChapters'
import { getWorkflowProgress } from '../../api/workspace'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { routeHref } from '../../router'
import type { NovelChapterList, NovelChapterRow } from '../../types/novelChapters'
import { NovelLinkStart } from './NovelLinkStart'
import { chaptersHeadline, rowMeta, rowTitle } from './stages/novelChapters'
import './stages/novelChapters.css'

const PAGE = 100

// A novel is text that already exists, so its title page skips the video
// stepper: one chapter list and the four things done to it. The stage routes
// stay reachable by link for what this page leaves out.
export function NovelTitlePage({ id, drama }: { id: number; drama: DramaDetail }) {
  const [list, setList] = useState<NovelChapterList | null>(null)
  const [rows, setRows] = useState<NovelChapterRow[]>([])
  const [error, setError] = useState<unknown>(null)
  // Unknown until answered, then the title's line count: with no lines yet, Translate and Read have nothing to open.
  const [lineCount, setLineCount] = useState<number | null>(null)
  // Lines with no translation yet: the link box stays until translating has begun, so a chain cut off before the glossary can resume.
  const [untranslated, setUntranslated] = useState(0)
  // Bumped while the link chain works so the list and counts follow what the server now holds.
  const [tick, setTick] = useState(0)
  const refresh = () => setTick((t) => t + 1)
  const title = drama.title_en || drama.title_zh || `Title #${id}`

  useEffect(() => {
    let cancelled = false
    getRawChapters(id, 0, PAGE).then(
      (l) => {
        if (cancelled) return
        setList(l)
        setRows(l.chapters)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [id, tick])

  useEffect(() => {
    let cancelled = false
    getWorkflowProgress(id).then(
      (p) => {
        if (cancelled) return
        setLineCount(p.line_count)
        setUntranslated(p.untranslated_count)
      },
      () => !cancelled && setLineCount(null),
    )
    return () => {
      cancelled = true
    }
  }, [id, tick])

  // One page at a time: the API refuses a page above its own maximum, so a long novel can't be fetched in one call.
  const showMore = () =>
    getRawChapters(id, rows.length, PAGE).then(
      (l) => setRows((r) => [...r, ...l.chapters]),
      (e: unknown) => setError(e),
    )
  const marks = !!list && list.translation_chars > 0
  // The Media stage is where saved chapters become lines, so a title without lines sends Translate there.
  const hasLines = lineCount !== null && lineCount > 0
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
      {list && lineCount !== null && (
        <NovelLinkStart
          id={id}
          chapters={list.total}
          translating={lineCount > 0 && untranslated < lineCount}
          defaultUrl={drama.source_url ?? ''}
          onProgress={refresh}
        />
      )}
      <div className="actions">
        <ButtonLink href={stage(hasLines ? 'translate' : 'source')} variant={hasLines ? 'primary' : 'secondary'}>Translate</ButtonLink>
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
            <button type="button" onClick={showMore}>Show more chapters</button>
            <span className="muted">{rows.length.toLocaleString()} of {list.total.toLocaleString()}</span>
          </div>
        )}
      </section>
    </section>
  )
}
