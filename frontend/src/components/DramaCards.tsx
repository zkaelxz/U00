import type { DramaSummary } from '../api/types'
import { routeHref } from '../router'

interface Props {
  items: DramaSummary[]
  selectedId: number | null
  onSelect: (id: number) => void
}

// Phone layout of the drama list: the title opens the workspace directly;
// "Details" is the secondary path (opens the detail panel).
export function DramaCards({ items, selectedId, onSelect }: Props) {
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
