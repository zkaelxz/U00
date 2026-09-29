import { useEffect, useRef, useState } from 'react'

import { api, ApiError } from '../api/client'
import { getFilterOptions } from '../api/library'
import type { DramaSummary } from '../api/types'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { languageLabel, mediaTypeLabel, statusLabel } from '../labels'
import { MAX_SELECTION, selectAllVisible, toggleId } from '../pages/libraryAdmin/libraryAdmin'
import { MEDIA_TYPES, SOURCE_LANGUAGES } from '../pages/libraryForm'
import type { LibraryFilterOptions } from '../types/library'
import { DramaCards } from './DramaCards'
import './libraryParity.css'
import {
  NO_MORE_FILTERS, listQuery, moreFilterCount, toggleTag, withCurrent,
  type MoreFilters,
} from './libraryFilters'

// Same choices the Streamlit Library tab offers.
const STATUSES = ['', 'not started', 'aligned', 'translated', 'dubbed', 'exported']
const QUICK_FILTERS = ['', 'Favorite', 'On Hold', 'Plan to Translate']

interface Props {
  selectedId: number | null
  onSelect: (id: number) => void
  // Bump to refetch after a create/delete elsewhere on the page.
  reloadKey?: number
  // Library admin selection (omit for a plain list). Desktop: a checkbox
  // column; phone: a Select toggle that turns cards into checkboxes.
  checked?: ReadonlySet<number>
  onCheckedChange?: (next: Set<number>) => void
  selectMode?: boolean
  onSelectModeChange?: (on: boolean) => void
  // Every successful load (for pruning the selection and the admin bar).
  onItems?: (items: DramaSummary[]) => void
}

export function LibraryList({
  selectedId, onSelect, reloadKey = 0, checked, onCheckedChange, selectMode, onSelectModeChange, onItems,
}: Props) {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [quickFilter, setQuickFilter] = useState('')
  const [more, setMore] = useState<MoreFilters>(NO_MORE_FILTERS)
  const [items, setItems] = useState<DramaSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const phone = useMediaQuery('(max-width: 640px)')
  const onItemsRef = useRef(onItems)
  useEffect(() => {
    onItemsRef.current = onItems
  })
  const selecting = !!checked && !!onCheckedChange
  const toggle = (id: number) => checked && onCheckedChange?.(toggleId(checked, id))
  const allChecked = !!items?.length && !!checked && items.slice(0, MAX_SELECTION).every((d) => checked.has(d.id))

  useEffect(() => {
    let cancelled = false
    // Debounced so typing in the search box doesn't send a request per key.
    const timer = setTimeout(() => {
      api
        .listDramas(listQuery(search, status, quickFilter, more))
        .then((resp) => {
          if (!cancelled) {
            setItems(resp.items)
            setError(null)
            onItemsRef.current?.(resp.items)
          }
        })
        .catch((e: unknown) => {
          if (!cancelled) setError(e instanceof ApiError ? e.message : 'Unexpected error.')
        })
    }, 200)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [search, status, quickFilter, more, reloadKey])

  return (
    <section className="panel wide" aria-labelledby="library-heading">
      <h2 id="library-heading">Library</h2>
      <div className="filters">
        <input
          type="search"
          placeholder="Search title/summary"
          aria-label="Search title or summary"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s || 'Any status'}
            </option>
          ))}
        </select>
        <select
          aria-label="Quick filter"
          value={quickFilter}
          onChange={(e) => setQuickFilter(e.target.value)}
        >
          {QUICK_FILTERS.map((q) => (
            <option key={q} value={q}>
              {q || 'No quick filter'}
            </option>
          ))}
        </select>
      </div>
      <MoreFiltersFold value={more} onChange={setMore} reloadKey={reloadKey} />

      {error && <p className="error" role="alert">{error}</p>}
      {!error && items === null && <p className="muted">Loading…</p>}
      {!error && items !== null && (
        <>
          <div className="list-head">
            <p className="muted" data-testid="drama-count">
              {items.length} drama(s)
            </p>
            {selecting && phone && items.length > 0 && onSelectModeChange && !selectMode && (
              <button type="button" onClick={() => onSelectModeChange(true)}>
                Select
              </button>
            )}
            {selecting && phone && items.length > 0 && selectMode && (
              <button
                type="button"
                onClick={() => onCheckedChange?.(allChecked ? new Set() : selectAllVisible(items).ids)}
              >
                {allChecked ? 'Select none' : 'Select all'}
              </button>
            )}
          </div>
          {selecting && items.length > MAX_SELECTION && (
            <p className="muted">At most {MAX_SELECTION} at a time.</p>
          )}
          {items.length > 0 && phone && (
            <DramaCards
              items={items}
              selectedId={selectedId}
              onSelect={onSelect}
              selectMode={selecting && selectMode}
              checked={checked}
              onToggle={toggle}
            />
          )}
          {items.length > 0 && !phone && (
            <table>
              <thead>
                <tr>
                  {selecting && (
                    <th className="check-col">
                      <label className="check-hit">
                        <input
                          type="checkbox"
                          aria-label="Select all visible"
                          checked={allChecked}
                          onChange={() => onCheckedChange?.(allChecked ? new Set() : selectAllVisible(items).ids)}
                        />
                      </label>
                    </th>
                  )}
                  <th>Title</th>
                  <th>Type</th>
                  <th>Lang</th>
                  <th>Status</th>
                  <th>Tags</th>
                </tr>
              </thead>
              <tbody>
                {items.map((d) => (
                  <tr
                    key={d.id}
                    className={d.id === selectedId ? 'selected' : undefined}
                    onClick={() => onSelect(d.id)}
                  >
                    {selecting && (
                      <td className="check-col" onClick={(e) => e.stopPropagation()}>
                        <label className="check-hit">
                          <input
                            type="checkbox"
                            aria-label={`Select ${d.title_en || d.title_zh || `#${d.id}`}`}
                            checked={!!checked?.has(d.id)}
                            onChange={() => toggle(d.id)}
                          />
                        </label>
                      </td>
                    )}
                    <td>
                      <button type="button" className="link" onClick={() => onSelect(d.id)}>
                        {d.title_en || d.title_zh || `#${d.id}`}
                      </button>
                      {d.title_en && d.title_zh && <div className="muted">{d.title_zh}</div>}
                    </td>
                    <td>{mediaTypeLabel(d.media_type)}</td>
                    <td>{languageLabel(d.source_language)}</td>
                    <td>{statusLabel(d.status)}</td>
                    <td>{d.custom_tags.join(', ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  )
}

// Studio, author, voice actor, language, type and custom tags. The data-driven
// choices load when the fold is first opened (and again after a create/delete).
function MoreFiltersFold({ value, onChange, reloadKey }: {
  value: MoreFilters
  onChange: (next: MoreFilters) => void
  reloadKey: number
}) {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<LibraryFilterOptions | null>(null)
  const [error, setError] = useState(false)
  useEffect(() => {
    if (!open) return
    let cancelled = false
    getFilterOptions().then(
      (o) => {
        if (!cancelled) {
          setOptions(o)
          setError(false)
        }
      },
      () => !cancelled && setError(true),
    )
    return () => {
      cancelled = true
    }
  }, [open, reloadKey])

  const n = moreFilterCount(value)
  const set = (key: Exclude<keyof MoreFilters, 'tags'>) => (e: { target: { value: string } }) =>
    onChange({ ...value, [key]: e.target.value })
  const pick = (label: string, key: 'studio' | 'author' | 'voice_actor', choices: string[] | undefined) => (
    <select aria-label={label} value={value[key]} onChange={set(key)}>
      <option value="">Any {label.toLowerCase()}</option>
      {withCurrent(choices ?? [], value[key]).map((c) => (
        <option key={c} value={c}>{c}</option>
      ))}
    </select>
  )
  return (
    <details className="more-filters" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>More filters{n > 0 && ` (${n})`}</summary>
      <div className="filters">
        {pick('Studio', 'studio', options?.studios)}
        {pick('Author', 'author', options?.authors)}
        {pick('Voice actor', 'voice_actor', options?.voice_actors)}
        <select aria-label="Language" value={value.source_language} onChange={set('source_language')}>
          <option value="">Any language</option>
          {SOURCE_LANGUAGES.map((l) => (
            <option key={l} value={l}>{languageLabel(l)}</option>
          ))}
        </select>
        <select aria-label="Type" value={value.media_type} onChange={set('media_type')}>
          <option value="">Any type</option>
          {withCurrent(MEDIA_TYPES, value.media_type).map((t) => (
            <option key={t} value={t}>{mediaTypeLabel(t)}</option>
          ))}
        </select>
      </div>
      {options && options.custom_tags.length > 0 && (
        <fieldset className="tag-filter">
          <legend>Custom tags (all must match)</legend>
          {options.custom_tags.map((t) => (
            <label key={t} className="check">
              <input
                type="checkbox"
                checked={value.tags.includes(t)}
                onChange={() => onChange({ ...value, tags: toggleTag(value.tags, t) })}
              />
              {t}
            </label>
          ))}
        </fieldset>
      )}
      {error && <p className="muted">Couldn't load the studio, author, voice actor and tag choices.</p>}
      {n > 0 && (
        <button type="button" className="link" onClick={() => onChange(NO_MORE_FILTERS)}>
          Clear these filters
        </button>
      )}
    </details>
  )
}
