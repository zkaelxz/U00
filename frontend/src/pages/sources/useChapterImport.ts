import { sourceImportJobId, startChapterImport } from '../../api/sourcesImport'
import { usePersistedState } from '../../hooks/usePersistedState'
import type { SeriesChapter } from '../../types/sources'
import type { ChapterImportResult } from '../../types/sourcesImport'
import { importIds, importReason } from './urlImportFormat'
import { useSourcesJob } from './useSourcesJob'

// The chapter import for one open series: the remembered drama and the
// sourceimport_<drama> job (see ChapterImport.tsx).
export function useChapterImport(source: string, seriesId: string) {
  // Remembered per series, so the next batch goes to the same drama.
  const [stored, setStored] = usePersistedState<number>(`sources.importInto.${source}:${seriesId}`, 0)
  const dramaId = typeof stored === 'number' && stored > 0 ? stored : null
  const job = useSourcesJob<ChapterImportResult>(dramaId ? sourceImportJobId(dramaId) : null)
  const running = job.status === 'running'
  const shownResult =
    job.startedHere && job.status === 'done' && job.result?.kind === 'chapter_import' ? job.result : null

  const start = (chapters: SeriesChapter[], selected: string[]) => {
    const ids = importIds(selected, chapters)
    if (!dramaId || importReason(ids.length, dramaId) || running) return
    job.start(() => startChapterImport(source, { series_id: seriesId, chapter_ids: ids, drama_id: dramaId }))
  }
  return { dramaId, setDramaId: (id: number | null) => setStored(id ?? 0), job, running, shownResult, start }
}

export type ChapterImportState = ReturnType<typeof useChapterImport>
