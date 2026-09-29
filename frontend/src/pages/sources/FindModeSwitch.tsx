/*
 * FindModeSwitch: the "Search by title | Paste a link" segmented control at
 * the top of the Sources card. Native radios (transparent, covering their
 * segment) inside labels, so arrow keys move between the two and a screen reader hears a
 * radio group; the labels are the 44px-tall segments.
 */
import { useId } from 'react'

export type FindMode = 'link' | 'search'

const MODES: { value: FindMode; label: string }[] = [
  { value: 'search', label: 'Search by title' },
  { value: 'link', label: 'Paste a link' },
]

export function FindModeSwitch({ value, onChange }: { value: FindMode; onChange: (m: FindMode) => void }) {
  const name = useId()
  return (
    <div className="segmented sources-mode" role="radiogroup" aria-label="Find by">
      {MODES.map((m) => (
        <label key={m.value} className={value === m.value ? 'on' : undefined}>
          <input
            type="radio"
            name={name}
            value={m.value}
            checked={value === m.value}
            onChange={() => onChange(m.value)}
          />
          {m.label}
        </label>
      ))}
    </div>
  )
}
