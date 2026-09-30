/*
 * NovelUrlImport: a pasted novel chapter link -> its text, appended to a
 * novel drama's novel text (S-5 R2; job sourceimport_<drama>). Options:
 * the AI fallback (SO09, AiFallback) and "Check before importing" (SO10).
 * When Baihe is unsure, or the check was asked for, nothing is written and
 * the Review extraction step opens below (ExtractionReview). With page
 * source pasted after a verification page (`html`, SO03), the text is read
 * from the paste instead (POST /api/sources/url/import-pasted), without the
 * AI fallback or the review step.
 */
import { useState } from 'react'

import { startNovelUrlImport } from '../../api/sourcesExtraction'
import { sourceImportJobId } from '../../api/sourcesImport'
import { startPastedImport } from '../../api/sourcesTools'
import { ButtonLink } from '../../components/Button'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { UrlImportResult } from '../../types/sourcesImport'
import { AiFallback } from './AiFallback'
import { DramaPicker } from './DramaPicker'
import { ExtractionReview } from './ExtractionReview'
import { AI_OFF, REVIEW_FIRST_HELP, aiReason, aiRequestFields } from './extractionFormat'
import { describeSourceError, percent } from './sourcesFormat'
import { useAiEngines } from './useAiEngines'
import { useDramaList } from './useDramaList'
import { useSourcesJob } from './useSourcesJob'
import { chapterImportDramas, urlImportText } from './urlImportFormat'

type Props = { url: string; html?: string | null; title: string; language: string | null }

export function NovelUrlImport({ url, html = null, title, language }: Props) {
  const dramas = useDramaList()
  const [dramaId, setDramaId] = useState<number | null>(null)
  // No reattach on 409: the server answers 409 while any job for the drama
  // runs, so the running one may not be this import; its text shows instead.
  const job = useSourcesJob<UrlImportResult>(dramaId ? sourceImportJobId(dramaId) : null, { reattachOn409: false })
  const running = job.status === 'running'
  const result = job.startedHere && job.status === 'done' && job.result?.kind === 'url_import' ? job.result : null
  const failed = job.startedHere && job.status === 'error' ? job.error : null
  const [aiChoice, setAiChoice] = useState(AI_OFF)
  const [reviewFirst, setReviewFirst] = useState(false)
  // The drama whose review is shown (closed: null).
  const [reviewing, setReviewing] = useState<number | null>(null)
  const ai = useAiEngines(aiChoice.on)
  const aiBlocked = html ? null : aiReason(aiChoice, ai.engines)

  const start = () => {
    if (!dramaId || running || aiBlocked) return
    setReviewing(dramaId)
    job.start(() =>
      html
        ? startPastedImport(url, html, dramaId)
        : startNovelUrlImport(url, dramaId, { ...aiRequestFields(aiChoice, ai.engines), ...(reviewFirst ? { review: true } : {}) }),
    )
  }
  const showReview = result?.review_open && dramaId !== null && reviewing === dramaId

  return (
    <div className="sources-import" role="group" aria-label="Import text">
      <DramaPicker
        dramas={dramas.items ? chapterImportDramas(dramas.items, false) : null}
        value={dramaId}
        onChange={setDramaId}
        disabled={running}
        newDrama={{ title, language, comic: false }}
        onCreated={dramas.add}
        help="The chapter text is added to the end of the drama’s novel text."
      />
      <ErrorBanner error={dramas.error} />
      {!html && (
        <>
          <AiFallback value={aiChoice} onChange={setAiChoice} engines={ai.engines} error={ai.error} disabled={running} />
          <div className="toggle-list">
            <Field label="Check before importing" help={REVIEW_FIRST_HELP}>
              <Toggle checked={reviewFirst} disabled={running} onChange={setReviewFirst} />
            </Field>
          </div>
        </>
      )}
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={!dramaId || running || !!aiBlocked} onClick={start}>
          {running ? 'Importing…' : 'Import text'}
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
          <p className={result.needs_review ? 'warn' : undefined} data-testid="url-import-result">
            {urlImportText(result)}{' '}
            {dramaId && !result.needs_review && (
              <ButtonLink href={`#/drama/${dramaId}/source`} size="sm">
                Open workspace
              </ButtonLink>
            )}
          </p>
        )}
      </div>
      {showReview && <ExtractionReview dramaId={dramaId} onClose={() => setReviewing(null)} />}
    </div>
  )
}
