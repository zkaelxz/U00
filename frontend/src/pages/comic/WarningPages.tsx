// Pages with warnings in the Translate flow: grouped list, Previous/Next, and the always-visible menu in the reader bar.

import { buttonClass } from '../../components/uiClasses'
import { neighbourWarning, type WarningGroup, type WarningPage } from './scanlateLogic'

// boxes: the numbers of the text boxes the warning is about, so the page can mark them.
export type GoToWarning = (ordinal: number, boxes: number[]) => void

interface Props {
  all: WarningPage[]
  groups: WarningGroup[]
  pageNumber: number | null
  onGo?: GoToWarning
}

function PageRow({ w, onGo }: { w: WarningPage; onGo?: GoToWarning }) {
  return (
    <li>
      <button type="button" className={buttonClass('secondary')} onClick={() => onGo?.(w.ordinal, w.boxes)}>
        Page {w.ordinal}
      </button>{' '}
      <span className={w.level === 'error' ? 'error' : 'warn'}>{w.text}</span>
    </li>
  )
}

function Stepper({ all, pageNumber, onGo }: Pick<Props, 'all' | 'pageNumber' | 'onGo'>) {
  const prev = neighbourWarning(all, pageNumber, -1)
  const next = neighbourWarning(all, pageNumber, 1)
  return (
    <div className="actions scanlate-warning-nav">
      <button type="button" className={buttonClass('secondary')} disabled={!prev} onClick={() => prev && onGo?.(prev.ordinal, prev.boxes)}>
        {prev ? `Previous warning (page ${prev.ordinal})` : 'Previous warning'}
      </button>
      <button type="button" className={buttonClass('secondary')} disabled={!next} onClick={() => next && onGo?.(next.ordinal, next.boxes)}>
        {next ? `Next warning (page ${next.ordinal})` : 'Next warning'}
      </button>
    </div>
  )
}

function Groups({ groups, onGo }: Pick<Props, 'groups' | 'onGo'>) {
  return (
    <div className="scanlate-warning-groups">
      {groups.map((g) => {
        const rows = (
          <ul>
            {g.pages.map((w) => <PageRow key={w.pageId} w={w} onGo={onGo} />)}
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
  )
}

export function WarningPages({ all, groups, pageNumber, onGo }: Props) {
  if (all.length === 0) return null
  return (
    <div className="scanlate-problem-pages stack">
      <Stepper all={all} pageNumber={pageNumber} onGo={onGo} />
      <h3>{all.length} page{all.length === 1 ? ' has' : 's have'} warnings</h3>
      <Groups groups={groups} onGo={onGo} />
    </div>
  )
}

// Lives in the reader's sticky bar, so the list is one click away while scrolling the pages.
export function WarningMenu({ all, groups, pageNumber, onGo }: Props) {
  if (all.length === 0) return null
  return (
    <details className="comic-warning-menu">
      <summary className={buttonClass('secondary')}>
        Warnings ({all.length})
      </summary>
      <div className="comic-warning-pop scanlate-problem-pages stack">
        <Stepper all={all} pageNumber={pageNumber} onGo={onGo} />
        <Groups groups={groups} onGo={onGo} />
      </div>
    </details>
  )
}
