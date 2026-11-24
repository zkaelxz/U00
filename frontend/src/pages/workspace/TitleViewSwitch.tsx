import type { TitleView } from './titleView'
import { isMember, useSession } from '../../hooks/useSession'

// A member keeps the Simple page (owner decision 2026-11-24: their settings are chosen for them), so there is nothing to switch.
export function TitleViewSwitch({ view, onChange }: { view: TitleView; onChange: (v: TitleView) => void }) {
  const member = isMember(useSession())
  if (member) return null
  return (
    <fieldset className="segmented title-view-switch">
      <legend className="visually-hidden">Title view</legend>
      {(['simple', 'full'] as const).map((v) => (
        <label key={v}>
          <input type="radio" name="title-view" value={v} checked={view === v} onChange={() => onChange(v)} />
          <span>{v === 'simple' ? 'Guided' : 'All tools'}</span>
        </label>
      ))}
    </fieldset>
  )
}
