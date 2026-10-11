/*
 * Tabs: the row of tabs above a page's panels (Settings, Diagnostics).
 *
 *   <Tabs label="Settings" idPrefix="settings" tabs={[{ id: 'a', label: 'A' }]} active="a" onSelect={set} />
 *   <div id={tabPanelId('settings', 'a')} role="tabpanel" aria-labelledby={tabButtonId('settings', 'a')} hidden={active !== 'a'}>
 *
 * Props
 *   tabs      id + label; optional `count` (a neutral number, e.g. search hits) and
 *             `attention` (a warning number, e.g. problems; announced in the tab's name)
 *   idPrefix  makes the DOM ids; the panels use tabButtonId / tabPanelId with the same prefix
 *   onSelect  called on click and on the arrow keys, Home and End (the arrows wrap)
 *
 * Phones: the row scrolls sideways inside itself, so the page never does.
 */
import type { KeyboardEvent } from 'react'
import './tabs.css'
import { tabButtonId, tabPanelId } from './tabIds'
import { badgeClass } from './uiClasses'

export type TabInfo<T extends string = string> = { id: T; label: string; count?: number; attention?: number }

export function Tabs<T extends string>({ tabs, active, onSelect, label, idPrefix }: {
  tabs: TabInfo<T>[]
  active: T
  onSelect: (id: T) => void
  label: string
  idPrefix: string
}) {
  function onKeyDown(ev: KeyboardEvent<HTMLDivElement>) {
    const at = tabs.findIndex((t) => t.id === active)
    const last = tabs.length - 1
    const to = { ArrowRight: at === last ? 0 : at + 1, ArrowLeft: at === 0 ? last : at - 1, Home: 0, End: last }[ev.key as 'Home']
    if (to === undefined) return
    ev.preventDefault()
    onSelect(tabs[to].id)
    document.getElementById(tabButtonId(idPrefix, tabs[to].id))?.focus()
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
          {t.label}
          {t.count !== undefined && <span className="badge section-count">{t.count}</span>}
          {!!t.attention && (
            <span className={badgeClass('warn')} title={`${t.attention} to look at`}>
              {t.attention}<span className="visually-hidden"> to look at</span>
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
