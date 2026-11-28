import { useContext, useState } from 'react'

import { coverThumbUrl } from '../api/metadata'
import type { DramaSummary } from '../api/types'
import { nameLang } from '../hanScript'
import { mediaTypeLabel } from '../labels'
import { Badge } from './Badge'
import { ButtonLink } from './Button'
import { cardNext, cardProgress, dramaName, newChaptersText, unreadChaptersText, readAction, ShowCoversContext, shownTags, tileHue, tileText, workspaceHref } from './libraryView'
import { SharingControl } from './SharingControl'
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
  // Titles with a saved reading position: their Watch / Listen / Read button says Continue.
  continuing?: ReadonlySet<number>
}

// The Library grid: a title tile, the title (the card's main action, opens
// the workspace; its hit area covers the whole card), badges, and quiet
// Watch, Listen or Read / Details buttons above it. No primaries.
// On phones the CSS turns each card into a row (tile left, text right).
export function DramaCards({ items, selectedId, onSelect, selectMode, checked, onToggle, continuing }: Props) {
  const showCovers = useContext(ShowCoversContext)
  if (selectMode && onToggle) return <SelectCards items={items} checked={checked} onToggle={onToggle} />
  return (
    <ul className="drama-grid">
      {items.map((d) => {
        const title = dramaName(d)
        const next = cardNext(d)
        const read = readAction(d, continuing?.has(d.id))
        return (
          <li key={d.id} className={d.id === selectedId ? 'drama-card selected' : 'drama-card'}>
            {showCovers && <DramaTile d={d} />}
            <div className="drama-card-main">
              <a className="drama-card-title" href={workspaceHref(d.id)} title={title} lang={nameLang(title, d)}>{title}</a>
              <CardText d={d} />
              <div className="drama-card-foot">
                <ButtonLink variant="ghost" size="sm" href={read.href} aria-label={`${read.label} ${title}`} className="drama-card-read">
                  {read.label}
                </ButtonLink>
                <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => onSelect(d.id)} aria-label={`Details: ${title}`}>
                  Details
                </button>
                {next && (
                  <ButtonLink variant="ghost" size="sm" href={next.href} aria-label={`${next.label} ${title}`} className="drama-card-next">
                    {next.label}
                  </ButtonLink>
                )}
              </div>
              <SharingControl kind="drama" id={d.id} title={title} isPrivate={d.is_private} ownedByMe={d.owned_by_me} seriesId={d.series_id} />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

// A cover that fails to load falls back to the letter tile, so a missing file never leaves a blank card.
export function DramaTile({ d }: { d: DramaSummary }) {
  const [broken, setBroken] = useState(false)
  const showCovers = useContext(ShowCoversContext)
  return (
    <div className={`drama-tile ${tileHue(d.id)}`} aria-hidden="true" lang={d.title_zh ? nameLang(d.title_zh, d) : undefined}>
      {showCovers && d.has_cover_art && !broken
        ? <img src={coverThumbUrl(d.id)} alt="" loading="lazy" onError={() => setBroken(true)} />
        : tileText(d)}
    </div>
  )
}

function CardText({ d }: { d: DramaSummary }) {
  const { shown, more } = shownTags(d.custom_tags)
  const progress = cardProgress(d)
  return (
    <>
      {d.title_en && d.title_zh && <p className="drama-card-orig" lang={nameLang(d.title_zh, d)}>{d.title_zh}</p>}
      {d.media_type && <p className="drama-card-type">{mediaTypeLabel(d.media_type)}</p>}
      {d.release_date && <p className="drama-card-type" data-testid="card-release-date">{d.release_date}</p>}
      <div className="pill-row">
        {newChaptersText(d.new_chapters) && <Badge>{newChaptersText(d.new_chapters)}</Badge>}
        {unreadChaptersText(d.unread_chapters) && <Badge>{unreadChaptersText(d.unread_chapters)}</Badge>}
        {d.status && <Badge kind="status" value={d.status} />}
        {d.source_language && <Badge kind="language" value={d.source_language} />}
        {shown.map((t) => <Badge key={t}>{t}</Badge>)}
        {more > 0 && <Badge>+{more}</Badge>}
      </div>
      {progress && (
        <p className="drama-card-progress" data-testid="card-progress">
          <span className="drama-card-bar" aria-hidden="true"><span style={{ width: `${Math.round(progress.share * 100)}%` }} /></span>
          <span>{progress.text}</span>
        </p>
      )}
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
        // The card (li) toggles selection on click; without stopPropagation on the checkbox label a click on it would toggle twice.
        return (
          <li key={d.id} className={on ? 'drama-card selected' : 'drama-card'} onClick={() => onToggle(d.id)}>
            <DramaTile d={d} />
            <label className="card-check" onClick={(e) => e.stopPropagation()}>
              <input type="checkbox" checked={on} onChange={() => onToggle(d.id)} aria-label={`Select ${title}`} />
            </label>
            <div className="drama-card-main">
              <span className="drama-card-title" lang={nameLang(title, d)}>{title}</span>
              <CardText d={d} />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
