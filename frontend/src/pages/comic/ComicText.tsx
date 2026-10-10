/*
 * The current page's lines in reading order, numbered like the boxes drawn
 * on the page. A side panel on desktop, a bottom Sheet on phones.
 */
import { ApiError } from '../../api/client'
import { isRegionsError, orderedLines, type RegionsState } from './comicLogic'

export function ComicLines({ page, state }: { page: number; state: RegionsState | undefined | 'none' }) {
  if (state === 'none') return <p className="muted">No text found on page {page}. If it has not been read yet, run Translate / detect on this page.</p>
  if (!state) return <p className="muted">Loading page {page} text…</p>
  if (isRegionsError(state)) {
    const forbidden = state.error instanceof ApiError && state.error.status === 403
    return (
      <p className="muted" role="alert">
        {forbidden ? 'Seeing the text needs permission to read lines — ask the owner.' : 'Could not load the text for this page.'}
      </p>
    )
  }
  const lines = orderedLines(state.regions)
  if (lines.length === 0) return <p className="muted">No text found on page {page}. If it has not been read yet, run Translate / detect on this page.</p>
  return (
    <ol className="comic-lines" data-testid="comic-lines">
      {lines.map((l, i) => (
        <li key={l.idx}>
          <span className="comic-box-num" aria-hidden="true">{i + 1}</span>
          <div>
            <div>{l.text}</div>
            {l.source && l.source.trim() !== l.text && <div className="muted comic-line-source">{l.source}</div>}
          </div>
        </li>
      ))}
    </ol>
  )
}
