/*
 * "Save N chapters as CBZ" for the open comic series: the ticked chapters
 * are written as CBZ files into the save folder on the PC (the saved_comics
 * folder in the app's data folder, or one picked in Settings), without
 * a drama; they are read in the app under Library tools. One save runs at a time (job
 * sources_save); the server answers per chapter (saved / already saved /
 * failed / not found) and never returns a path. A finished result is shown
 * only for a run started here: the job id is shared by every series.
 */
import { SAVE_JOB_ID, startChapterSave } from '../../api/sourcesImport'
import { OpenFolderButton } from '../manga/SaveFolder'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { SeriesChapter } from '../../types/sources'
import type { ChapterSaveResult } from '../../types/sourcesImport'
import { describeSourceError, percent } from './sourcesFormat'
import { importIds, saveLabel, saveOutcomeText, saveOutcomeTone, saveReason, saveSummary } from './urlImportFormat'
import { useSourcesJob } from './useSourcesJob'

type Props = {
  source: string
  seriesId: string
  display: string
  chapters: SeriesChapter[]
  selected: string[]
  // An import is running for this series: its job holds the source's pace.
  busy: boolean
}

export function ChapterSave({ source, seriesId, display, chapters, selected, busy }: Props) {
  const job = useSourcesJob<ChapterSaveResult>(SAVE_JOB_ID, { reattachOn409: false })
  const running = job.startedHere && job.status === 'running'
  const ids = importIds(selected, chapters)
  const reason = saveReason(ids.length)
  const result = job.startedHere && job.status === 'done' && job.result?.kind === 'chapter_save' ? job.result : null
  const failed = job.startedHere && job.status === 'error' ? job.error : null
  const start = () => {
    if (reason || running || busy) return
    job.start(() => startChapterSave(source, { series_id: seriesId, chapter_ids: ids }))
  }
  return (
    <div className="sources-save" role="group" aria-label="Save as CBZ" data-testid="save-cbz">
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={!!reason || running || busy} onClick={start}>
          {running ? 'Saving…' : saveLabel(ids.length)}
        </button>
        {running ? (
          <button type="button" className={buttonClass('secondary')} onClick={() => void job.cancel()}>
            Cancel
          </button>
        ) : (
          <>
            <span className="muted">
              Saved to the manga folder on the PC. <a href="#/manga">Read saved manga</a>
            </span>
            <OpenFolderButton size="sm" />
          </>
        )}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      <div aria-live="polite">
        {running && (
          <p data-testid="save-progress">
            {job.message || 'Saving…'}
            {percent(job.progress)}
          </p>
        )}
        {failed && <p className="warn" role="alert">{describeSourceError(failed, display).text}</p>}
        {result && (
          <div className="sources-outcomes" data-testid="save-outcomes">
            <p className={result.partial ? 'warn' : undefined}>
              <strong>{saveSummary(result)}</strong>
            </p>
            {!!result.handoff && (
              <p className="warn">{display} showed a browser check, so the rest was not saved. Try again later.</p>
            )}
            <ul className="sources-outcome-list">
              {result.chapters.map((c) => (
                <li key={c.chapter_id}>
                  <span>{c.title || c.chapter_id}</span>
                  <span className={saveOutcomeTone(c.outcome)}>{saveOutcomeText(c)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
