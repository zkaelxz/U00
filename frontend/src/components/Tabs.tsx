/*
 * Tabs: the row of tabs above a page's panels (Settings, Diagnostics).
 *
 *   <Tabs label="Settings" idPrefix="settings" tabs={[{ id: 'a', label: 'A' }]} active="a" onSelect={set} />
 *   <div id={tabPanelId('settings', 'a')} role="tabpanel" aria-labelledby={tabButtonId('settings', 'a')} hidden={active !== 'a'}>
 *
 * Props
 *   tabs      id + label; optional `count` (a neutral number, e.g. search hits) and
 *             `attention` (a warning number, e.g. problems; announced in the tab's name) and
 *             `unsaved` (a small dot for edits not saved yet; announced in the tab's name) and
 *             `short` (what a phone shows when the full label is too long for a row; the full label stays the
 *             accessible name)
 *   idPrefix  makes the DOM ids; the panels use tabButtonId / tabPanelId with the same prefix
 *   onSelect  called on click and on the arrow keys, Home and End (the arrows wrap)
 *
 * Phones (640px and narrower): four tabs or fewer stay one row. Five or more become one picker button that names
 * the current section and opens a list, so no label is cut or scrolled out of sight. `keepRow` opts out, for a tab
 * row inside a sticky panel whose height cannot change (Review tools).
 */
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useMediaQuery } from '../hooks/useMediaQuery'
import './tabs.css'
import { tabButtonId, tabPanelId } from './tabIds'
import { badgeClass } from './uiClasses'

export type TabInfo<T extends string = string> = { id: T; label: string; short?: string; count?: number; attention?: number; unsaved?: boolean }

// Four tabs still fit a 390px row with short labels; five do not.
export const PICKER_MIN_TABS = 5
const PHONE_QUERY = '(max-width: 640px)'

// On a phone a tab with a short label shows it and keeps the full label in the accessible name; elsewhere the page holds
// the full label only, so its text is never doubled.
export function TabLabel<T extends string>({ t, phone }: { t: TabInfo<T>; phone: boolean }) {
  if (!phone || !t.short) return <>{t.label}</>
  return (
    <>
      <span className="ui-tab-full">{t.label}</span>
      <span className="ui-tab-short" aria-hidden="true">{t.short}</span>
    </>
  )
}

function TabExtras<T extends string>({ t }: { t: TabInfo<T> }) {
  return (
    <>
      {t.count !== undefined && <span className="badge section-count">{t.count}</span>}
      {t.unsaved && (
        <span className="ui-tab-dot" data-testid={`tab-unsaved-${t.id}`}>
          <span className="visually-hidden"> (unsaved changes)</span>
        </span>
      )}
      {!!t.attention && (
        <span className={badgeClass('warn')} title={`${t.attention} to look at`}>
          {t.attention}<span className="visually-hidden"> to look at</span>
        </span>
      )}
    </>
  )
}

// The phone form of a long tab bar. The panels name themselves from tabButtonId, so each tab keeps a hidden label
// under that id while the picker is shown.
export function TabPicker<T extends string>({ tabs, active, onSelect, label, idPrefix }: {
  tabs: TabInfo<T>[]
  active: T
  onSelect: (id: T) => void
  label: string
  idPrefix: string
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const current = tabs.find((t) => t.id === active) ?? tabs[0]
  useEffect(() => {
    if (!open) return
    const away = (ev: Event) => { if (!root.current?.contains(ev.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [open])
  function pick(id: T) {
    setOpen(false)
    if (id !== active) onSelect(id)
  }
  return (
    <div className="ui-tab-picker" ref={root} onKeyDown={(ev) => { if (ev.key === 'Escape') setOpen(false) }}>
      {tabs.map((t) => <span key={t.id} id={tabButtonId(idPrefix, t.id)} hidden>{t.label}</span>)}
      <button
        type="button"
        className="btn ui-tab-picker-btn"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${label}: ${current.label}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="ui-tab-picker-lead" aria-hidden="true">Section</span>
        <span className="ui-tab-picker-current" aria-hidden="true">{current.label}</span>
        <TabExtras t={current} />
        <span className="ui-tab-picker-caret" aria-hidden="true">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div role="listbox" aria-label={label} className="ui-tab-picker-list">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="option"
              aria-selected={t.id === active}
              className="ui-tab-picker-option"
              onClick={() => pick(t.id)}
            >
              <span>{t.label}</span>
              <TabExtras t={t} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Tabs<T extends string>({ tabs, active, onSelect, label, idPrefix, keepRow }: {
  tabs: TabInfo<T>[]
  active: T
  onSelect: (id: T) => void
  label: string
  idPrefix: string
  keepRow?: boolean
}) {
  const phone = useMediaQuery(PHONE_QUERY)
  function onKeyDown(ev: KeyboardEvent<HTMLDivElement>) {
    const at = tabs.findIndex((t) => t.id === active)
    const last = tabs.length - 1
    const to = { ArrowRight: at === last ? 0 : at + 1, ArrowLeft: at === 0 ? last : at - 1, Home: 0, End: last }[ev.key as 'Home']
    if (to === undefined) return
    ev.preventDefault()
    onSelect(tabs[to].id)
    document.getElementById(tabButtonId(idPrefix, tabs[to].id))?.focus()
  }
  if (phone && !keepRow && tabs.length >= PICKER_MIN_TABS) {
    return <TabPicker tabs={tabs} active={active} onSelect={onSelect} label={label} idPrefix={idPrefix} />
  }
  return (
    <div role="tablist" aria-label={label} className="ui-tabs" onKeyDown={onKeyDown}>
      {tabs.map((t) => (
        <button
          key={t.id}
          id={tabButtonId(idPrefix, t.id)}
          type="button"
          role="tab"
          aria-selected={t.id === active}
          aria-controls={tabPanelId(idPrefix, t.id)}
          tabIndex={t.id === active ? 0 : -1}
          className="ui-tab"
          onClick={() => onSelect(t.id)}
        >
          <TabLabel t={t} phone={phone} />
          <TabExtras t={t} />
        </button>
      ))}
    </div>
  )
}
