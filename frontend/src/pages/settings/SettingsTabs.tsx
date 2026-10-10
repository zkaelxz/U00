import type { KeyboardEvent } from 'react'
import type { SettingsTab } from './settingsIndex'

export type TabInfo = { id: SettingsTab; label: string; count?: number }

export const tabId = (id: SettingsTab) => `settings-tab-${id}`
export const panelId = (id: SettingsTab) => `settings-panel-${id}`

export function SettingsTabs({ tabs, active, onSelect }: { tabs: TabInfo[]; active: SettingsTab; onSelect: (id: SettingsTab) => void }) {
  function onKeyDown(ev: KeyboardEvent<HTMLDivElement>) {
    const at = tabs.findIndex((t) => t.id === active)
    const last = tabs.length - 1
    const to = { ArrowRight: at === last ? 0 : at + 1, ArrowLeft: at === 0 ? last : at - 1, Home: 0, End: last }[ev.key as 'Home']
    if (to === undefined) return
    ev.preventDefault()
    onSelect(tabs[to].id)
    document.getElementById(tabId(tabs[to].id))?.focus()
  }
  return (
    <div role="tablist" aria-label="Settings" className="settings-tabs" onKeyDown={onKeyDown}>
      {tabs.map((t) => (
        <button
          key={t.id}
          id={tabId(t.id)}
          type="button"
          role="tab"
          aria-selected={t.id === active}
          aria-controls={panelId(t.id)}
          tabIndex={t.id === active ? 0 : -1}
          className="settings-tab"
          onClick={() => onSelect(t.id)}
        >
          {t.label}
          {t.count !== undefined && <span className="badge section-count">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}
