import { useEffect, useRef, useState } from 'react'

import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { OpenSeries, SeriesResult, SourceSummary, TrackedSeries } from '../../types/sources'
import { ImportBar, ImportSetup, TrackRow } from './ChapterImport'
import { ChapterSave } from './ChapterSave'
import { useChapterImport } from './useChapterImport'
import { SourceErrorLine } from './SearchPanel'
import {
  CHAPTERS_PAGE, describeSourceError, groupChapters, limitGroups, percent, safeHref, seriesExtra, seriesLinks, seriesMeta,
  type SeriesView,
} from './sourcesFormat'
import type { SourcesJob } from './useSourcesJob'
import {
  IMPORT_REMOTE_ALLOWED, allSelected, chapterMarks, selectAllLabel, selectableChapters, toggleId, type ChapterMark,
} from './urlImportFormat'

type Props = {
  open: OpenSeries
  display: string
  job: SourcesJob<SeriesResult>
  // What may be shown for `open` (the job can hold another series).
  view: SeriesView
  tracked: TrackedSeries | null
  remote: boolean
  // Chapter list dropped after the source's Adult works switch changed.
  cleared: boolean
  // Another series was loading and has now stopped: offer Try again.
  busyEnded: boolean
  // Changes each time a series is opened: focus moves to the heading.
  focusKey: number
  // Phone/tablet: the panel replaces the results and starts with "‹ Results".
  showBack: boolean
  onReload: () => void
  onClose: () => void
  onUntrack: (t: TrackedSeries) => void
  untrackBusy: boolean
  // Chapter import and Track (S-4). Without sourceInfo there is no import.
  sourceInfo?: SourceSummary | null
  // A chapter to tick when the series opens (from a pasted chapter link).
  preselect?: string | null
  phone?: boolean
  onTracked?: (list: TrackedSeries[]) => void
}

// A long description is clamped to 3 lines with More/Less.
const LONG_DESCRIPTION = 180

export function SeriesPanel({
  open, display, job, view, tracked, remote, cleared, busyEnded, focusKey, showBack, onReload, onClose, onUntrack, untrackBusy,
  sourceInfo, preselect, phone = false, onTracked,
}: Props) {
  const headRef = useRef<HTMLHeadingElement>(null)
  const [more, setMore] = useState(false)
  const [shown, setShown] = useState(CHAPTERS_PAGE)
  // "Cancel it" was accepted for the other series; reset whenever busyOther changes.
  const [stopAsked, setStopAsked] = useState(false)
  const [busySeen, setBusySeen] = useState(view.busyOther)
  if (busySeen !== view.busyOther) {
    setBusySeen(view.busyOther)
    setStopAsked(false)
  }
  const askStop = () => {
    void job.cancel().then((ok) => ok && setStopAsked(true))
  }

  useEffect(() => {
    if (focusKey) headRef.current?.focus({ preventScroll: false })
  }, [focusKey])

  const result = !cleared && view.status === 'done' ? view.result : null
  const info = result?.info ?? null
  const chapters = result?.chapters ?? []
  const groups = groupChapters(chapters)
  const title = info?.title || open.title || open.series_id
  const running = view.status === 'running'
  const siteUrl = safeHref(info?.url)
  const description = info?.description?.trim() ?? ''
  const extra = seriesExtra(info)
  const links = seriesLinks(info)
  // Import (S-4): ticked chapter ids, the drama and the import job.
  const [selected, setSelected] = useState<string[]>([])
  // A pasted chapter link ticks its chapter, also when the series is already open.
  const [preselectSeen, setPreselectSeen] = useState<string | null>(null)
  const pre = preselect ?? null
  if (pre !== preselectSeen) {
    setPreselectSeen(pre)
    if (pre) setSelected((cur) => toggleId(cur, pre, true))
  }
  const canImport = !!sourceInfo?.import_supported && (!remote || IMPORT_REMOTE_ALLOWED)
  const imp = useChapterImport(open.source, open.series_id, !!sourceInfo?.supports.get_pages, canImport)
  const importing = !!result && chapters.length > 0 && canImport
  // Tracking (R4) works for any source, not only those with import; same remote rule.
  const canTrack = !!result && !tracked && (!remote || IMPORT_REMOTE_ALLOWED)
  // Step 107: chapters already in the chosen drama are marked and left out of Select all.
  const selectable = selectableChapters(chapters, imp.importState.state)

  return (
    <section className="card sources-series" aria-label="Series">
      {showBack && (
        <button type="button" className={buttonClass('ghost', 'md', 'sources-back')} onClick={onClose}>
          ‹ Results
        </button>
      )}
      <div className="sources-series-head">
        <h3 ref={headRef} tabIndex={-1}>
          {title}
        </h3>
        {tracked && <Badge tone="accent">Tracked</Badge>}
      </div>

      {view.busyOther ? (
        <div className="sources-busy">
          <p className="warn" role="alert">
            Another series from {display} is still loading.
          </p>
          <p aria-live="polite" className="muted">
            {stopAsked ? 'Asked the other series to stop. Try again in a moment.' : ''}
          </p>
          <div className="actions">
            {!stopAsked && (
              <button type="button" className={buttonClass('secondary')} onClick={askStop}>
                Cancel it
              </button>
            )}
            <button type="button" className={buttonClass('secondary')} onClick={onReload}>
              Try again
            </button>
          </div>
        </div>
      ) : (
        <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      )}

      <div aria-live="polite">
        {running && (
          <p>
            {job.message || 'Loading the series…'}
            {percent(job.progress)} ·{' '}
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={job.cancel}>
              Cancel
            </button>
          </p>
        )}
      </div>

      {busyEnded && (
        <p className="muted">
          The other series stopped loading.{' '}
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={onReload}>
            Try again
          </button>
        </p>
      )}

      {cleared && <p className="muted">Chapter list cleared; reload it to use the new setting.</p>}

      {!cleared && view.status === 'error' && !!view.error && (
        <SourceErrorLine copy={{ ...describeSourceError(view.error, display, remote), retry: true }} onRetry={onReload} />
      )}

      {result && (
        <>
          <p className="sources-meta">{seriesMeta(display, info, chapters.length)}</p>
          {extra && <p className="muted">{extra}</p>}
          {description && (
            <div className="sources-description">
              <p className={more ? '' : 'clamp'}>{description}</p>
              {description.length > LONG_DESCRIPTION && (
                <button type="button" className={buttonClass('ghost', 'sm', 'sources-more')} aria-expanded={more} onClick={() => setMore((m) => !m)}>
                  {more ? 'Less' : 'More'}
                </button>
              )}
            </div>
          )}
          {links.length > 0 && (
            <div className="sources-links" role="group" aria-label="Download links">
              <p className="muted">
                Posted with this work. Open them yourself: the app never downloads from them. Then add the EPUB
                in Workspace → Source → Novel text → Attach EPUB.
              </p>
              <ul>
                {links.map((l) => (
                  <li key={l.url}>
                    <a href={l.url} target="_blank" rel="noopener noreferrer" title={l.url}>
                      {l.label} ↗
                    </a>
                    {l.password && (
                      <span>
                        Code <code>{l.password}</code>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className="actions">
        {siteUrl && (
          <ButtonLink href={siteUrl} target="_blank" rel="noopener noreferrer">
            Open on site ↗
          </ButtonLink>
        )}
        {!running && (result || cleared) && (
          <button type="button" className={buttonClass('secondary')} onClick={onReload}>
            Reload
          </button>
        )}
        {tracked && (
          <ConfirmButton
            label="Stop tracking…"
            verb="stop tracking"
            name={tracked.title || title}
            busy={untrackBusy}
            onConfirm={() => onUntrack(tracked)}
          />
        )}
        <button type="button" className={buttonClass('ghost')} onClick={onClose}>
          Close
        </button>
      </div>

      {importing && (
        <ImportSetup
          imp={imp}
          source={open.source}
          seriesId={open.series_id}
          display={display}
          comic={!!sourceInfo?.supports.get_pages}
          title={title}
          language={info?.language ?? null}
          tracked={tracked}
          onTracked={(list) => onTracked?.(list)}
        />
      )}
      {!importing && canTrack && (
        <TrackRow source={open.source} seriesId={open.series_id} dramaId={null} onTracked={(list) => onTracked?.(list)} />
      )}
      {result && chapters.length > 0 && !canImport && sourceInfo?.import_supported && remote && (
        <p className="muted">Importing chapters is PC only for now.</p>
      )}

      {result && chapters.length > 0 && (
        <Section title="Chapters" count={chapters.length} defaultOpen storageKey="sources.chapters">
          {importing && (
            <label className="sources-select-all">
              <input
                type="checkbox"
                checked={allSelected(selected, selectable)}
                disabled={imp.running}
                onChange={(e) => setSelected(e.target.checked ? selectable.map((c) => c.chapter_id) : [])}
              />
              {selectAllLabel(selectable.length, chapters.length)}
            </label>
          )}
          <ChapterList
            groups={groups}
            shown={shown}
            selected={importing ? selected : undefined}
            marks={importing ? chapterMarks(imp.importState.state) : undefined}
            disabled={imp.running}
            onToggle={(id, on) => setSelected((cur) => toggleId(cur, id, on))}
          />
          {chapters.length > shown && (
            <button type="button" className={buttonClass('secondary')} onClick={() => setShown(chapters.length)}>
              Show all {chapters.length}
            </button>
          )}
        </Section>
      )}
      {importing && <ImportBar imp={imp} chapters={chapters} selected={selected} phone={phone} />}
      {importing && sourceInfo?.supports.get_pages && (
        <ChapterSave
          source={open.source}
          seriesId={open.series_id}
          display={display}
          chapters={chapters}
          selected={selected}
          busy={imp.running}
        />
      )}
      {showBack && result && (
        <button type="button" className={buttonClass('ghost', 'md', 'sources-back')} onClick={onClose}>
          ‹ Results
        </button>
      )}
    </section>
  )
}

function ChapterList({ groups, shown, selected, marks, disabled, onToggle }: {
  groups: ReturnType<typeof groupChapters>
  shown: number
  // Import: tick boxes when given.
  selected?: string[]
  // Import: Imported / Failed / Not attempted in the chosen drama.
  marks?: Map<string, ChapterMark>
  disabled?: boolean
  onToggle?: (id: string, on: boolean) => void
}) {
  const many = groups.length > 1
  return (
    <div className="sources-chapters">
      {limitGroups(groups, shown).map((g) => (
        <div key={g.group || '-'}>
          {many && <h4>{g.group || 'Other'}</h4>}
          <ul className={selected ? 'sources-pick' : undefined}>
            {g.chapters.map((c) => (
              <li key={c.chapter_id}>
                {selected ? (
                  <label>
                    <input
                      type="checkbox"
                      checked={selected.includes(c.chapter_id)}
                      disabled={disabled}
                      onChange={(e) => onToggle?.(c.chapter_id, e.target.checked)}
                    />
                    {c.title || c.chapter_id}
                  </label>
                ) : (
                  c.title || c.chapter_id
                )}
                {marks?.has(c.chapter_id) && <MarkTag mark={marks.get(c.chapter_id)!} />}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function MarkTag({ mark }: { mark: ChapterMark }) {
  return (
    <>
      <Badge tone={mark.tone}>{mark.label}</Badge>
      {mark.note && <span className="sources-mark-note">{mark.note}</span>}
    </>
  )
}
