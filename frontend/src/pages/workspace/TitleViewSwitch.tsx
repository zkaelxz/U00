import type { TitleView } from './titleView'

export function TitleViewSwitch({ view, onChange }: { view: TitleView; onChange: (v: TitleView) => void }) {
  return (
    <fieldset className="segmented title-view-switch">
      <legend className="visually-hidden">View</legend>
      {(['simple', 'full'] as const).map((v) => (
        <label key={v}>
          <input type="radio" name="title-view" value={v} checked={view === v} onChange={() => onChange(v)} />
          <span>{v === 'simple' ? 'Simple' : 'Full'}</span>
        </label>
      ))}
    </fieldset>
  )
}
