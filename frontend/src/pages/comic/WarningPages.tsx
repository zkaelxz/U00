// The Translate panel's list of pages with warnings: grouped by kind, harmless groups folded, with next/previous.

import { buttonClass } from '../../components/uiClasses'
import { neighbourWarning, type WarningGroup, type WarningPage } from './scanlateLogic'

interface Props {
  all: WarningPage[]
  groups: WarningGroup[]
  pageNumber: number | null
  onGoToPage?: (ordinal: number) => void
}

function PageRow({ w, onGoToPage }: { w: WarningPage; onGoToPage?: (ordinal: number) => void }) {
  return (
    <li>
      <button type="button" className={buttonClass('secondary')} onClick={() => onGoToPage?.(w.ordinal)}>
        Page {w.ordinal}
      </button>{' '}
      <span className={w.level === 'error' ? 'error' : 'warn'}>{w.text}</span>
    </li>
  )
}

export function WarningPages({ all, groups, pageNumber, onGoToPage }: Props) {
  if (all.length === 0) return null
  const prev = neighbourWarning(all, pageNumber, -1)
  const next = neighbourWarning(all, pageNumber, 1)
  return (
    <div className="scanlate-problem-pages stack">
      <div className="actions scanlate-warning-nav">
        <button type="button" className={buttonClass('secondary')} disabled={!prev} onClick={() => prev && onGoToPage?.(prev.ordinal)}>
          {prev ? `Previous warning (page ${prev.ordinal})` : 'Previous warning'}
        </button>
        <button type="button" className={buttonClass('secondary')} disabled={!next} onClick={() => next && onGoToPage?.(next.ordinal)}>
          {next ? `Next warning (page ${next.ordinal})` : 'Next warning'}
        </button>
      </div>
      <h3>{all.length} page{all.length === 1 ? ' has' : 's have'} warnings</h3>
      <div className="scanlate-warning-groups">
        {groups.map((g) => {
          const rows = (
            <ul>
              {g.pages.map((w) => <PageRow key={w.pageId} w={w} onGoToPage={onGoToPage} />)}
            </ul>
          )
          const title = `${g.title} (${g.pages.length})`
          return g.harmless ? (
            <details key={g.kind}>
              <summary>{title}</summary>
              {rows}
            </details>
          ) : (
            <section key={g.kind}>
              <h4>{title}</h4>
              {rows}
            </section>
          )
        })}
      </div>
    </div>
  )
}
