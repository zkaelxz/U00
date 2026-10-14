/*
 * The chapters the title's source has, inside the Chapters & pages sheet. A
 * site the app has an adapter for lists its chapters and imports one on
 * request; a site only the browser extension reaches lists the links the
 * extension saw, each opened on the site to be sent with the extension.
 * Nothing is fetched until the sheet opens, and one chapter is imported at a time.
 */
import { useEffect, useState } from 'react'

import { comicApi } from '../../api/comic'
import { startChapterImport } from '../../api/sourcesImport'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useJob } from '../../hooks/useJob'
import type { ComicSourceChapters } from '../../types/comic'
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

  const { done } = useJob(jobId, {
    onDone: () => {
      setJobId(null)
      setReloads((n) => n + 1)
      onImported()
    },
  })
  const running = jobId !== null && !done

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

  if (!list || list.mode === 'none' || list.chapters.length === 0) {
    return error ? <ErrorBanner error={error} onDismiss={() => setError(null)} /> : null
  }
  const missing = list.chapters.filter((c) => !c.imported).length
  return (
    <div className="stack" data-testid="comic-source-chapters">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <strong>Chapters on the source ({missing} not imported)</strong>
      {list.mode === 'links' && (
        <p className="muted">Open a chapter on its site and send it with the extension.</p>
      )}
      <ul className="comic-source-chapters">
        {list.chapters.map((c) => (
          <li key={c.chapter_id || c.url}>
            <span>{c.title || c.chapter_id}</span>{' '}
            {c.imported ? (
              <span className="muted">Imported</span>
            ) : list.mode === 'adapter' ? (
              <button type="button" disabled={running} onClick={() => void importOne(c.chapter_id)}>
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
