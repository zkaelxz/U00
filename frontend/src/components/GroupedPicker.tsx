// A themed stand-in for a grouped <select>: Android's own option dialog ignores the app theme.
import { useId, useState } from 'react'

import { Sheet } from './Sheet'
import './groupedPicker.css'

export type PickerGroup = { label: string; options: { value: string; label: string }[] }

type PickerProps = {
  title: string
  value: string
  groups: PickerGroup[]
  disabled?: boolean
  onPick: (value: string) => void
  // Field passes these to its single child.
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean
}

export function GroupedPicker({ title, value, groups, disabled, onPick, ...aria }: PickerProps) {
  const [open, setOpen] = useState(false)
  const currentId = useId()
  const all = groups.flatMap((g) => g.options)
  const current = all.find((o) => o.value === value) ?? all[0]
  return (
    <>
      <button type="button" className="picker-btn" disabled={disabled} aria-haspopup="dialog" onClick={() => setOpen(true)} {...aria} aria-describedby={[aria['aria-describedby'], currentId].filter(Boolean).join(' ')}>
        <span id={currentId} className="picker-current">{current?.label ?? ''}</span>
        <span className="picker-caret" aria-hidden="true">▾</span>
      </button>
      <Sheet open={open} title={title} onClose={() => setOpen(false)}>
        {groups.map((g) => (
          <section key={g.label} className="picker-group" aria-label={g.label}>
            <h3 className="picker-group-label">{g.label}</h3>
            <ul className="sheet-menu">
              {g.options.map((o) => (
                <li key={o.value}>
                  <button
                    type="button"
                    className="picker-option"
                    aria-current={o.value === value ? 'true' : undefined}
                    onClick={() => { setOpen(false); onPick(o.value) }}
                  >
                    <span className="picker-option-label">{o.label}</span>
                    {o.value === value && <span aria-hidden="true">✓</span>}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </Sheet>
    </>
  )
}
