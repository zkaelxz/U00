import { useEffect, useState } from 'react'

import { getNovelGlossary } from '../../api/autotuneGlossary'
import { getLineChapters, getRawChapters } from '../../api/novelChapters'
import { getWorkflowProgress } from '../../api/workspace'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Tabs } from '../../components/Tabs'
import { browserStorage } from '../../hooks/usePersistedState'
import { loadPrefs } from '../reader/readerPrefsStore'
import { routeHref } from '../../router'
import type { NovelChapterList, NovelChapterRow, NovelLineChapterRow } from '../../types/novelChapters'
import { TitleViewSwitch } from './TitleViewSwitch'
import type { TitleView } from './titleView'
import { NovelInstructionsTab } from './NovelInstructionsTab'
import { NovelAddChapters } from './NovelAddChapters'
import { NovelLinkStart } from './NovelLinkStart'
import { NovelCastCard } from './stages/NovelCastCard'
import { NovelChapterRetranslate } from './stages/NovelChapterRetranslate'
import { mainButton } from './novelLinkChain'
import { chaptersHeadline, lineProgress, readerPageOf, rowMeta, rowTitle } from './stages/novelChapters'
import './stages/novelChapters.css'

const PAGE = 100

// A novel is text that already exists, so its title page skips the video
// stepper: one chapter list and the four things done to it. The stage routes
// stay reachable by link for what this page leaves out.
export function NovelTitlePage({ id, drama, onView }: { id: number; drama: DramaDetail; onView: (v: TitleView) => void }) {
  const [list, setList] = useState<NovelChapterList | null>(null)
  const [rows, setRows] = useState<NovelChapterRow[]>([])
  const [error, setError] = useState<unknown>(null)
  // Unknown until answered, then the title's line count: with no lines yet, Translate and Read have nothing to open.
  const [lineCount, setLineCount] = useState<number | null>(null)
  // Per saved chapter: how many of its lines are translated. Empty until the title has lines.
  const [lineRows, setLineRows] = useState<Map<number, NovelLineChapterRow>>(new Map())
  const [tab, setTab] = useState<'chapters' | 'instructions'>('chapters')
  // Lines with no translation yet: the link box stays until translating has begun, so a chain cut off before the glossary can resume.
  const [untranslated, setUntranslated] = useState(0)
  const [scanDone, setScanDone] = useState(false)
  // Bumped while the link chain works so the list and counts follow what the server now holds.
  const [tick, setTick] = useState(0)
  // Raised by Translate when the title has chapters but no lines, so the link chain starts from where the title is.
  const [startSignal, setStartSignal] = useState(0)
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

  useEffect(() => {
    let cancelled = false
    // The cast card reads the finished glossary scan; without one there is nothing to offer.
    getNovelGlossary(id).then(
      (g) => !cancelled && setScanDone(g.status === 'done'),
      () => !cancelled && setScanDone(false),
    )
    return () => {
      cancelled = true
    }
  }, [id, tick])

  useEffect(() => {
    let cancelled = false
    getLineChapters(id).then(
      (l) => !cancelled && setLineRows(new Map(l.chapters.map((c) => [c.number, c]))),
      // The counts are an extra: without them the rows fall back to the raw-text mark.
      () => !cancelled && setLineRows(new Map()),
    )
    return () => {
      cancelled = true
    }
  }, [id, tick])

  const [again, setAgain] = useState<number | null>(null)

  // One page at a time: the API refuses a page above its own maximum, so a long novel can't be fetched in one call.
  const showMore = () =>
    getRawChapters(id, rows.length, PAGE).then(
      (l) => setRows((r) => [...r, ...l.chapters]),
      (e: unknown) => setError(e),
    )
  const marks = !!list && list.translation_chars > 0
  const readerSize = loadPrefs(browserStorage()).chapterSize
  // With no lines yet the Translate tab has nothing to translate, so Translate runs the link chain (lines, speakers, glossary scan) instead.
  const hasLines = lineCount !== null && lineCount > 0
  // Everything has English: the next move is Review, so it takes the primary styling.
  const main = mainButton(lineCount, untranslated)
  const allTranslated = main === 'review'
  const stage = (s: string, tab?: 'glossary' | 'translate') => routeHref({ name: 'drama', id, stage: s, ...(tab ? { tab } : {}) })

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
        <TitleViewSwitch view="simple" onChange={onView} />
      </header>
      {list && lineCount !== null && lineCount > 0 && untranslated < lineCount && (
        <NovelAddChapters id={id} chapters={list.total} defaultUrl="" onProgress={refresh} />
      )}
      {list && lineCount !== null && (
        <NovelLinkStart
          id={id}
          chapters={list.total}
          translating={lineCount > 0 && untranslated < lineCount}
          defaultUrl={drama.source_url ?? ''}
          hasSeries={!!drama.series_id}
          startSignal={startSignal}
          onProgress={refresh}
        />
      )}
      <NovelCastCard dramaId={id} scanDone={scanDone} />
      <div className="actions">
        {allTranslated && <ButtonLink href={stage('review')} variant="primary">Review</ButtonLink>}
        {hasLines || !list || list.total === 0 ? (
          <ButtonLink href={hasLines ? stage('translate', 'translate') : stage('source')} variant={hasLines && !allTranslated ? 'primary' : 'secondary'}>Translate</ButtonLink>
        ) : (
          <button type="button" className={buttonClass('primary')} onClick={() => setStartSignal((n) => n + 1)}>Translate</button>
        )}
        <ButtonLink href={stage('source')}>Add chapters</ButtonLink>
        <ButtonLink href={stage('translate', 'glossary')}>Glossary</ButtonLink>
        <ButtonLink href={stage('export')}>Export</ButtonLink>
        <ButtonLink href={routeHref({ name: 'read', id, page: null })}>Read</ButtonLink>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <Tabs
        label="Title"
        idPrefix="novel-title"
        tabs={[{ id: 'chapters', label: 'Chapters' }, { id: 'instructions', label: 'Instructions' }]}
        active={tab}
        onSelect={setTab}
      />
      {tab === 'instructions' && <NovelInstructionsTab id={id} />}
      <section className="panel" aria-label="Saved chapters" hidden={tab !== 'chapters'}>
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
                  {lineRows.has(r.number) ? (
                    <span className={`chapters-mark${lineRows.get(r.number)!.translated > 0 ? ' chapters-mark-in' : ''}`}>
                      {lineProgress(lineRows.get(r.number)!)}
                    </span>
                  ) : (
                    marks && (
                      <span className={`chapters-mark${r.in_translation ? ' chapters-mark-in' : ''}`}>
                        {r.in_translation ? 'In translation text' : 'Not in translation text'}
                      </span>
                    )
                  )}
                </a>
                {lineRows.has(r.number) && (
                  <a
                    className="source-link"
                    href={routeHref({ name: 'read', id, page: readerPageOf(lineRows.get(r.number)!.first_idx, readerSize) })}
                    aria-label={`Read chapter ${r.number}: ${rowTitle(r)}`}
                  >
                    Read
                  </a>
                )}
                {lineRows.has(r.number) && lineRows.get(r.number)!.translated > 0 && (
                  <button
                    type="button"
                    className={buttonClass('ghost', 'sm')}
                    aria-label={`Translate chapter ${r.number} again: ${rowTitle(r)}`}
                    disabled={again !== null}
                    onClick={() => setAgain(r.number)}
                  >
                    Translate again
                  </button>
                )}
                {again === r.number && (
                  <NovelChapterRetranslate
                    dramaId={id}
                    number={r.number}
                    title={rowTitle(r)}
                    onClose={() => setAgain(null)}
                    onStarted={() => setTick((n) => n + 1)}
                  />
                )}
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
