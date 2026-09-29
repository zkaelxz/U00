import type { DramaSummary } from '../api/types'
import { mediaTypeLabel } from '../labels'
import { Badge } from './Badge'
import { ButtonLink } from './Button'
import { dramaName, readHref, shownTags, tileHue, tileText, workspaceHref } from './libraryView'
import { buttonClass } from './uiClasses'

interface Props {
  items: DramaSummary[]
  selectedId: number | null
  // Opens the drama's details (a Sheet on the Library page).
  onSelect: (id: number) => void
  // Select mode (Library admin): each card gets a 44px checkbox over its
  // tile and a tap on the card toggles it instead of navigating.
  selectMode?: boolean
  checked?: ReadonlySet<number>
  onToggle?: (id: number) => void
}

// The Library grid: a title tile, the title (the card's main action, opens
// the workspace; its hit area covers the whole card), badges, and quiet
// Read / Details buttons above it. No primaries.
// On phones the CSS turns each card into a row (tile left, text right).
export function DramaCards({ items, selectedId, onSelect, selectMode, checked, onToggle }: Props) {
  if (selectMode && onToggle) return <SelectCards items={items} checked={checked} onToggle={onToggle} />
  return (
    <ul className="drama-grid">
      {items.map((d) => {
        const title = dramaName(d)
        return (
          <li key={d.id} className={d.id === selectedId ? 'drama-card selected' : 'drama-card'}>
            <div className={`drama-tile ${tileHue(d.id)}`} aria-hidden="true">{tileText(d)}</div>
            <div className="drama-card-main">
              <a className="drama-card-title" href={workspaceHref(d.id)}>{title}</a>
              <CardText d={d} />
              <div className="drama-card-foot">
                <ButtonLink variant="ghost" size="sm" href={readHref(d)} aria-label={`Read ${title}`} className="drama-card-read">
                  Read
                </ButtonLink>
                <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onSelect(d.id)} aria-label={`Details: ${title}`}>
                  Details
                </button>
              </div>
            </div>
          </li>
        )
      })}
    </ul>
  )
}

function CardText({ d }: { d: DramaSummary }) {
  const { shown, more } = shownTags(d.custom_tags)
  return (
    <>
      {d.title_en && d.title_zh && <p className="drama-card-orig">{d.title_zh}</p>}
      {d.media_type && <p className="drama-card-type">{mediaTypeLabel(d.media_type)}</p>}
      <div className="pill-row">
        {d.status && <Badge kind="status" value={d.status} />}
        {d.source_language && <Badge kind="language" value={d.source_language} />}
        {shown.map((t) => <Badge key={t}>{t}</Badge>)}
        {more > 0 && <Badge>+{more}</Badge>}
      </div>
    </>
  )
}

function SelectCards({ items, checked, onToggle }: {
  items: DramaSummary[]; checked?: ReadonlySet<number>; onToggle: (id: number) => void
}) {
  return (
    <ul className="drama-grid selecting">
      {items.map((d) => {
        const title = dramaName(d)
        const on = !!checked?.has(d.id)
        return (
          <li key={d.id} className={on ? 'drama-card selected' : 'drama-card'} onClick={() => onToggle(d.id)}>
            <div className={`drama-tile ${tileHue(d.id)}`} aria-hidden="true">{tileText(d)}</div>
            <label className="card-check" onClick={(e) => e.stopPropagation()}>
              <input type="checkbox" checked={on} onChange={() => onToggle(d.id)} aria-label={`Select ${title}`} />
            </label>
            <div className="drama-card-main">
              <span className="drama-card-title">{title}</span>
              <CardText d={d} />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
