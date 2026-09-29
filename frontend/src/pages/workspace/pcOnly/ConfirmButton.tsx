// Private two-step confirm button (stand-in for components/ConfirmButton.tsx
// on branch react-library-admin; swap at merge time). The first press turns
// "Remove…" into "Confirm remove ‹name›" (danger) plus Cancel; the second
// press calls onConfirm. Reverts after 5 s, on Cancel, Escape or blur.

import { useEffect, useRef, useState } from 'react'

import { CONFIRM_TIMEOUT_MS, confirmStep, type ConfirmEvent } from './pcOnly'
import './pcOnly.css'

interface Props {
  label: string
  // Shown on the armed button, e.g. "Confirm remove audio/video".
  confirmLabel: string
  onConfirm: () => void
  disabled?: boolean
  // Plain-text reason shown next to a disabled button (never hover-only).
  disabledReason?: string | null
  // Accessible name for the unarmed button when the label alone is ambiguous
  // (e.g. one "Delete" per row).
  ariaLabel?: string
}

export function ConfirmButton({ label, confirmLabel, onConfirm, disabled = false, disabledReason, ariaLabel }: Props) {
  const [armed, setArmed] = useState(false)
  const wrap = useRef<HTMLSpanElement>(null)

  const send = (event: ConfirmEvent) => {
    const next = confirmStep(armed, event)
    setArmed(next.armed)
    if (next.fire) onConfirm()
  }

  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [armed])

  const isArmed = armed && !disabled
  const action = confirmLabel.replace(/^Confirm /, '')

  return (
    <span
      className="pc-confirm"
      ref={wrap}
      onKeyDown={(e) => {
        if (e.key === 'Escape') send('cancel')
      }}
      onBlur={(e) => {
        if (!wrap.current?.contains(e.relatedTarget as Node | null)) send('cancel')
      }}
    >
      <button
        type="button"
        className={isArmed ? 'danger' : undefined}
        disabled={disabled}
        aria-label={isArmed ? undefined : ariaLabel}
        onClick={() => send('press')}
      >
        {isArmed ? confirmLabel : `${label}…`}
      </button>
      {isArmed && (
        <button type="button" className="link" onClick={() => send('cancel')}>
          Cancel
        </button>
      )}
      <span className="muted pc-confirm-note" aria-live="polite">
        {isArmed ? `Press again to ${action}.` : disabled && disabledReason ? disabledReason : ''}
      </span>
    </span>
  )
}
