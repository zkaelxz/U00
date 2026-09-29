import { sourceImportJobId, startChapterImport } from '../../api/sourcesImport'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { SeriesChapter } from '../../types/sources'
import type { ChapterImportResult } from '../../types/sourcesImport'
import { chapterImportDramas, importIds, importReason } from './urlImportFormat'
import { useDramaList } from './useDramaList'
import { useSourcesJob } from './useSourcesJob'

// The chapter import for one open series: the drama list, the remembered
// drama and the sourceimport_<drama> job (see ChapterImport.tsx). Nothing
// loads while `enabled` is false (no import for this source or viewer).
export function useChapterImport(source: string, seriesId: string, comic: boolean, enabled: boolean) {
  const dramas = useDramaList(enabled)
  const choices = dramas.items ? chapterImportDramas(dramas.items, comic) : null
  // Remembered per series, so the next batch goes to the same drama. It
  // counts only once the list is in and still offers it (deleted, or no
  // longer the right media type: the picker would show "Choose a drama…").
  const [stored, setStored] = usePersistedState<number>(`sources.importInto.${source}:${seriesId}`, 0)
  const dramaId =
    typeof stored === 'number' && stored > 0 && choices?.some((d) => d.id === stored) ? stored : null
  // No reattach on 409: the server answers 409 while any job for the drama
  // runs, so the running one may not be an import (or not this one); the
  // server's text shows in startError instead.
  const job = useSourcesJob<ChapterImportResult>(dramaId ? sourceImportJobId(dramaId) : null, { reattachOn409: false })
  const running = job.status === 'running'
  const shownResult =
    job.startedHere && job.status === 'done' && job.result?.kind === 'chapter_import' ? job.result : null

  const start = (chapters: SeriesChapter[], selected: string[]) => {
    const ids = importIds(selected, chapters)
    if (!dramaId || importReason(ids.length, dramaId) || running) return
    job.start(() => startChapterImport(source, { series_id: seriesId, chapter_ids: ids, drama_id: dramaId }))
  }
  return {
    dramas, choices, dramaId, setDramaId: (id: number | null) => setStored(id ?? 0), job, running, shownResult, start,
  }
}

export type ChapterImportState = ReturnType<typeof useChapterImport>
