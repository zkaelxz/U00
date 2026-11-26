// Which stages a comic title skips, and why.
import type { StageId } from './stages'

// Comic bubbles live in Scanlate, not in lines, so these stages have nothing to show for them.
const LINE_STAGES: readonly StageId[] = ['translate', 'review', 'export']

export const COMIC_STAGE_TITLE = 'Comics are translated in Scanlate'

export function isLineStageForComic(stage: StageId | null): boolean {
  return stage !== null && LINE_STAGES.includes(stage)
}

export type PageCounts = { pages: number; chapters: number }

export const pagesSummary = (counts: PageCounts | null): string =>
  !counts
    ? 'Checking…'
    : counts.pages === 0
      ? 'No pages yet'
      : `${counts.pages} page${counts.pages === 1 ? '' : 's'}${counts.chapters ? `, ${counts.chapters} chapter${counts.chapters === 1 ? '' : 's'}` : ''}`
