/*
 * ComicUrlImport: a pasted comic chapter link -> its page images, added to
 * a manhua/manga/manhwa drama (parity SO06; job sourceimport_<drama>). The
 * images are checked first (size, shape, repeats across chapters) and the
 * ones left out are listed with the reason. The optional AI fallback
 * (AiFallback) only runs when that check is unsure. When the result needs
 * review nothing is added.
 */
import { useState } from 'react'

import { sourceImportJobId } from '../../api/sourcesImport'
import { startComicUrlImport } from '../../api/sourcesExtraction'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { ComicUrlImportResult } from '../../types/sourcesExtraction'
import { AiFallback } from './AiFallback'
import { DramaPicker } from './DramaPicker'
import { AI_OFF, aiReason, aiRequestFields, comicImportText, skippedTitle } from './extractionFormat'
import { describeSourceError, percent } from './sourcesFormat'
import { useAiEngines } from './useAiEngines'
import { useDramaList } from './useDramaList'
import { useSourcesJob } from './useSourcesJob'
import { chapterImportDramas } from './urlImportFormat'
import './extraction.css'

type Props = { url: string; title: string; language: string | null }

export function ComicUrlImport({ url, title, language }: Props) {
  const dramas = useDramaList()
  const [dramaId, setDramaId] = useState<number | null>(null)
  const [aiChoice, setAiChoice] = useState(AI_OFF)
  const ai = useAiEngines(aiChoice.on)
  const aiBlocked = aiReason(aiChoice, ai.engines)
  // No reattach on 409: the running job for the drama may be another one.
  const job = useSourcesJob<ComicUrlImportResult>(dramaId ? sourceImportJobId(dramaId) : null, { reattachOn409: false })
  const running = job.status === 'running'
  const result = job.startedHere && job.status === 'done' && job.result?.kind === 'comic_import' ? job.result : null
  const failed = job.startedHere && job.status === 'error' ? job.error : null

  const start = () => {
    if (!dramaId || running || aiBlocked) return
    job.start(() => startComicUrlImport(url, dramaId, aiRequestFields(aiChoice, ai.engines)))
  }
  const skipped = result ? skippedTitle(result) : null

  return (
    <div className="sources-import" role="group" aria-label="Import pages">
      <DramaPicker
        dramas={dramas.items ? chapterImportDramas(dramas.items, true) : null}
        value={dramaId}
        onChange={setDramaId}
        disabled={running}
        newDrama={{ title, language, comic: true }}
        onCreated={dramas.add}
        help="The pages are added after the drama’s existing pages."
      />
      <ErrorBanner error={dramas.error} />
      <AiFallback value={aiChoice} onChange={setAiChoice} engines={ai.engines} error={ai.error} disabled={running} />
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={!dramaId || running || !!aiBlocked} onClick={start}>
          {running ? 'Importing…' : 'Import pages'}
        </button>
        {running && (
          <button type="button" className={buttonClass('secondary')} onClick={() => void job.cancel()}>
            Cancel
          </button>
        )}
        {!dramaId && !running && <span className="muted">Still needed: a drama to import into.</span>}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      <div aria-live="polite">
        {running && <p>{job.message || 'Importing…'}{percent(job.progress)}</p>}
        {failed && <p className="warn" role="alert">{describeSourceError(failed, 'The site').text}</p>}
        {result && (
          <div data-testid="comic-import-result">
            <p className={result.needs_review ? 'warn' : undefined}>
              {comicImportText(result)}{' '}
              {dramaId && !result.needs_review && (
                <ButtonLink href={`#/comic/${dramaId}`} size="sm">
                  Open pages
                </ButtonLink>
              )}
            </p>
            {skipped && (
              <details className="sources-skipped">
                <summary>{skipped}</summary>
                <ul>
                  {result.skipped.map((s, i) => (
                    <li key={i}>
                      <span className="sources-link">{s.display_url ?? 'Image'}</span> — {s.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
