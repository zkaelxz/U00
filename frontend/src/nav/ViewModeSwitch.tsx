import type { ViewMode } from './viewMode'

export function ViewModeSwitch({ mode, onChange }: { mode: ViewMode; onChange: (m: ViewMode) => void }) {
  return (
    <fieldset className="segmented view-mode-switch">
      <legend className="visually-hidden">View</legend>
      {(['simple', 'advanced'] as const).map((m) => (
        <label key={m}>
          <input type="radio" name="view-mode" aria-label={m === 'simple' ? 'Menu: Simple' : 'Menu: Advanced'} value={m} checked={mode === m} onChange={() => onChange(m)} />
          <span>{m === 'simple' ? 'Simple' : 'Advanced'}</span>
        </label>
      ))}
    </fieldset>
  )
}
