/* The reader's per-page Show Original button. */
import type { ComicPageInfo } from '../../types/comic'

export function ShowOriginalButton({ page, typeset, originals, onToggle }: { page: ComicPageInfo | undefined; typeset: boolean; originals: ReadonlySet<number>; onToggle: (page: ComicPageInfo | undefined) => void }) {
  return (
    <button type="button" aria-pressed={!!page && originals.has(page.id)} disabled={!page?.has_rendered || !typeset} onClick={() => onToggle(page)}>
      Show Original
    </button>
  )
}
