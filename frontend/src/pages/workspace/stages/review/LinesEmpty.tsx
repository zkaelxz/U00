import { ButtonLink } from '../../../../components/Button'
import { buttonClass } from '../../../../components/uiClasses'
import { routeHref } from '../../../../router'
import type { LineFilter } from '../../../../types/review'
import { emptyMessage } from './reviewLogic'

interface Props {
  dramaId: number
  filter: LineFilter
  term: string
  searching: boolean
  jobRunning: boolean
  onAddFirst: () => void
  onAllLines: () => void
}

export function LinesEmpty({ dramaId, filter, term, searching, jobRunning, onAddFirst, onAllLines }: Props) {
  return (
    <div className="review-empty">
      <p className="muted">{emptyMessage(filter, term)}</p>
      {filter === 'all' && !searching ? (
        <div className="actions">
          <ButtonLink variant="primary" href={routeHref({ name: 'drama', id: dramaId, stage: 'source' })}>
            Go to Media
          </ButtonLink>
          <button type="button" className={buttonClass('secondary')} disabled={jobRunning} onClick={onAddFirst}>
            Add first line
          </button>
        </div>
      ) : (
        <button type="button" className={buttonClass('ghost')} onClick={onAllLines}>
          All lines
        </button>
      )}
    </div>
  )
}
