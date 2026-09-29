import type { DramaSummary } from '../api/types'
import { routeHref } from '../router'

interface Props {
  items: DramaSummary[]
  selectedId: number | null
  onSelect: (id: number) => void
  // Select mode (Library admin): each card gets a 44px checkbox and a tap
  // on the card toggles it instead of navigating.
  selectMode?: boolean
  checked?: ReadonlySet<number>
  onToggle?: (id: number) => void
}

// Phone layout of the drama list: the title opens the workspace directly;
// "Details" is the secondary path (opens the detail panel).
export function DramaCards({ items, selectedId, onSelect, selectMode, checked, onToggle }: Props) {
  if (selectMode && onToggle) return <SelectCards items={items} checked={checked} onToggle={onToggle} />
  return (
    <ul className="drama-cards">
      {items.map((d) => {
        const title = d.title_en || d.title_zh || `#${d.id}`
        const meta = [d.media_type?.replace(/_/g, ' '), d.source_language].filter(Boolean).join(' · ')
        return (
          <li key={d.id} className={d.id === selectedId ? 'selected' : undefined}>
            <a className="drama-card-title" href={routeHref({ name: 'drama', id: d.id, stage: 'source' })}>
              {title}
            </a>
            {d.title_en && d.title_zh && <div className="muted">{d.title_zh}</div>}
            <div className="drama-card-meta">
              {d.status && <span className="badge">{d.status}</span>}
              {meta && <span className="muted">{meta}</span>}
              <a className="drama-card-read" href={routeHref({ name: 'read', id: d.id, page: null })} aria-label={`Read ${title}`}>
                Read
              </a>
              <button type="button" onClick={() => onSelect(d.id)} aria-label={`Details: ${title}`}>
                Details
              </button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function SelectCards({ items, checked, onToggle }: {
  items: DramaSummary[]; checked?: ReadonlySet<number>; onToggle: (id: number) => void
}) {
  return (
    <ul className="drama-cards selecting">
      {items.map((d) => {
        const title = d.title_en || d.title_zh || `#${d.id}`
        const on = !!checked?.has(d.id)
        return (
          <li key={d.id} className={on ? 'selected' : undefined} onClick={() => onToggle(d.id)}>
            <label className="card-check" onClick={(e) => e.stopPropagation()}>
              <input type="checkbox" checked={on} onChange={() => onToggle(d.id)} aria-label={`Select ${title}`} />
              <span className="drama-card-title">{title}</span>
            </label>
            {d.title_en && d.title_zh && <div className="muted">{d.title_zh}</div>}
            {d.status && <span className="badge">{d.status}</span>}
          </li>
        )
      })}
    </ul>
  )
}
