import { sourceImportJobId, startAiRecover, startChapterImport } from '../../api/sourcesImport'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { SeriesChapter } from '../../types/sources'
import type { ChapterImportResult, UrlImportResult } from '../../types/sourcesImport'
import { MAX_CHAPTERS, chapterImportDramas, hiddenDramaCount, importIds, importReason, retryIds } from './urlImportFormat'
import { useDramaList } from './useDramaList'
import { useImportState } from './useImportState'
import { useSourcesJob } from './useSourcesJob'

// The chapter import for one open series: the drama list, the remembered
// drama and the sourceimport_<drama> job (see ChapterImport.tsx). Nothing
// loads while `enabled` is false (no import for this source or viewer).
export function useChapterImport(source: string, seriesId: string, comic: boolean, enabled: boolean) {
  const dramas = useDramaList(enabled)
  const choices = dramas.items ? chapterImportDramas(dramas.items, comic) : null
  const hiddenCount = dramas.items ? hiddenDramaCount(dramas.items, comic) : 0
  // Remembered per series, so the next batch goes to the same drama. It
  // counts only once the list is in and still offers it (deleted, or no
  // longer the right media type: the picker would show "Choose a drama…").
  const [stored, setStored] = usePersistedState<number>(`sources.importInto.${source}:${seriesId}`, 0)
  const dramaId =
    typeof stored === 'number' && stored > 0 && choices?.some((d) => d.id === stored) ? stored : null
  // No reattach on 409: the server answers 409 while any job for the drama
  // runs, so the running one may not be an import (or not this one); the
  // server's text shows in startError instead.
  const job = useSourcesJob<ChapterImportResult | UrlImportResult>(dramaId ? sourceImportJobId(dramaId) : null, { reattachOn409: false })
  const running = job.status === 'running'
  const shownResult =
    job.startedHere && job.status === 'done' && job.result?.kind === 'chapter_import' ? job.result : null

  const start = (chapters: SeriesChapter[], selected: string[]) => {
    const ids = importIds(selected, chapters)
    if (!dramaId || importReason(ids.length, dramaId) || running) return
    job.start(() => startChapterImport(source, { series_id: seriesId, chapter_ids: ids, drama_id: dramaId }))
  }
  // What's already in the drama, and "Retry failed chapters (N)":
  // exactly the retry ids (the server re-reads the list), at most 200 a run.
  const importState = useImportState(source, seriesId, dramaId, enabled, running)
  const retry = retryIds(importState.state, shownResult)
  const startRetry = () => {
    if (!dramaId || running || !retry.length) return
    const ids = retry.slice(0, MAX_CHAPTERS)
    job.start(() => startChapterImport(source, { series_id: seriesId, chapter_ids: ids, drama_id: dramaId }))
  }
  // One AI call on a chapter whose layout changed; its job ends in a Review extraction.
  const recover = (chapterId: string, engine: string) => {
    if (!dramaId || running) return
    job.start(() =>
      startAiRecover(source, chapterId, { series_id: seriesId, drama_id: dramaId, engine, confirm: true }),
    )
  }
  const recoveryReview = job.startedHere && job.status === 'done' && job.result?.kind === 'url_import' && job.result.review_open
  return {
    recover, recoveryReview,
    dramas, choices, hiddenCount, dramaId, setDramaId: (id: number | null) => setStored(id ?? 0), job, running, shownResult, start,
    importState, retry, startRetry,
  }
}

export type ChapterImportState = ReturnType<typeof useChapterImport>
