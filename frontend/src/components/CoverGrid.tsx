import type { DramaSummary } from '../api/types'
import { mediaTypeLabel } from '../labels'
import { DramaTile } from './DramaCards'
import { cardProgress, dramaName, readAction, workspaceHref } from './libraryView'
import { buttonClass } from './uiClasses'

interface Props {
  items: DramaSummary[]
  selectedId: number | null
  onSelect: (id: number) => void
  selectMode?: boolean
  checked?: ReadonlySet<number>
  onToggle?: (id: number) => void
  continuing?: ReadonlySet<number>
}

// The Library's cover shelf: big covers, the title and a progress bar under each.
export function CoverGrid({ items, selectedId, onSelect, selectMode, checked, onToggle, continuing }: Props) {
  const selecting = !!selectMode && !!onToggle
  return (
    <ul className={selecting ? 'cover-grid selecting' : 'cover-grid'}>
      {items.map((d) => {
        const title = dramaName(d)
        const progress = cardProgress(d)
        const on = !!checked?.has(d.id)
        const read = readAction(d, continuing?.has(d.id))
        const cover = <DramaTile d={d} />
        const bar = progress && (
          <span className="cover-bar" role="img" aria-label={progress.text} title={progress.text}>
            <span style={{ width: `${Math.round(progress.share * 100)}%` }} />
          </span>
        )
        if (selecting) {
          // The cell toggles selection; the checkbox stops the click so it is not counted twice.
          return (
            <li key={d.id} className={on ? 'cover-cell selected' : 'cover-cell'} onClick={() => onToggle(d.id)}>
              {cover}
              <label className="card-check" onClick={(e) => e.stopPropagation()}>
                <input type="checkbox" checked={on} onChange={() => onToggle(d.id)} aria-label={`Select ${title}`} />
              </label>
              <span className="cover-title">{title}</span>
              {bar}
            </li>
          )
        }
        return (
          <li key={d.id} className={d.id === selectedId ? 'cover-cell selected' : 'cover-cell'}>
            <a className="cover-link" href={workspaceHref(d.id)} title={title} aria-label={title}>{cover}</a>
            <a className="cover-title" href={workspaceHref(d.id)} title={title}>{title}</a>
            {d.media_type && <span className="cover-sub">{mediaTypeLabel(d.media_type)}</span>}
            {bar}
            <span className="cover-actions">
              <a className={buttonClass('ghost', 'sm')} href={read.href} aria-label={`${read.label} ${title}`}>{read.label}</a>
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onSelect(d.id)} aria-label={`Details: ${title}`}>
                Details
              </button>
            </span>
          </li>
        )
      })}
    </ul>
  )
}
