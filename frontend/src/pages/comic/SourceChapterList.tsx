/*
 * The chapters the title's source has, inside the Chapters & pages sheet. A
 * site the app has an adapter for lists its chapters through the paced series
 * job (started by the person, never on opening the sheet) and imports one on
 * request; a site only the browser extension reaches lists the links the
 * extension saw, each opened on the site to be sent with the extension.
 */
import { useEffect, useState } from 'react'

import { comicApi } from '../../api/comic'
import { seriesJobId, startSeries } from '../../api/sources'
import { startChapterImport } from '../../api/sourcesImport'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useJob } from '../../hooks/useJob'
import type { ComicSourceChapter, ComicSourceChapters } from '../../types/comic'
import type { SeriesResult } from '../../types/sources'
import { useSourcesJob } from '../sources/useSourcesJob'
import { SourceLink } from '../discover/ExternalLink'

type Props = { dramaId: number; onImported: () => void }

export function SourceChapterList({ dramaId, onImported }: Props) {
  const [list, setList] = useState<ComicSourceChapters | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let live = true
    comicApi.sourceChapters(dramaId).then(
      (r) => live && setList(r),
      (e: unknown) => live && setError(e),
    )
    return () => {
      live = false
    }
  }, [dramaId, reloads])

  const series = useSourcesJob<SeriesResult>(list?.mode === 'adapter' ? seriesJobId(list.source) : null, { reattachOn409: false })
  const { done } = useJob(jobId, {
    onDone: () => {
      setJobId(null)
      setReloads((n) => n + 1)
      onImported()
    },
  })
  const importing = jobId !== null && !done

  const importOne = async (chapterId: string) => {
    if (!list) return
    setError(null)
    try {
      const started = await startChapterImport(list.source, { series_id: list.series_id, chapter_ids: [chapterId], drama_id: dramaId })
      setJobId(started.job_id)
    } catch (e) {
      setError(e)
    }
  }

  if (!list || list.mode === 'none') {
    return error ? <ErrorBanner error={error} onDismiss={() => setError(null)} /> : null
  }

  // A series run left from another title must not show here.
  const loaded = series.result && series.result.series_id === list.series_id ? series.result : null
  const imported = new Set(list.imported_ids)
  const rows: ComicSourceChapter[] =
    list.mode === 'adapter'
      ? (loaded?.chapters ?? []).map((c) => ({ chapter_id: c.chapter_id, title: c.title, url: '', imported: imported.has(c.chapter_id) }))
      : list.chapters
  const missing = rows.filter((c) => !c.imported).length
  return (
    <div className="stack" data-testid="comic-source-chapters">
      <ErrorBanner error={error ?? series.startError ?? series.error} onDismiss={() => { setError(null); series.clearStartError() }} />
      {list.mode === 'adapter' && !loaded && (
        <button
          type="button"
          disabled={series.status === 'running'}
          onClick={() => series.start(() => startSeries(list.source, list.series_id))}
        >
          {series.status === 'running' ? 'Loading chapters…' : 'Load the source’s chapters'}
        </button>
      )}
      {rows.length > 0 && <strong>Chapters on the source ({missing} not imported)</strong>}
      {list.mode === 'links' && <p className="muted">Open a chapter on its site and send it with the extension.</p>}
      <ul className="comic-source-chapters">
        {rows.map((c) => (
          <li key={c.chapter_id || c.url}>
            <span>{c.title || c.chapter_id}</span>{' '}
            {c.imported ? (
              <span className="muted">Imported</span>
            ) : list.mode === 'adapter' ? (
              <button type="button" disabled={importing} onClick={() => void importOne(c.chapter_id)}>
                Import
              </button>
            ) : (
              <SourceLink href={c.url}>Open</SourceLink>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
