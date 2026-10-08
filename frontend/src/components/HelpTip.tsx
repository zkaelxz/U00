/*
 * HelpTip: the focusable (i) button and its tooltip, shown on hover, focus or click and hidden with
 * Escape or blur. Field uses it for a control's help; use it directly for a heading or a block.
 *
 *   <HelpTip label="Golden sets" id={helpId}>Steps…</HelpTip>
 *
 * `label` names the button ("Help: <label>"); `id` is the tooltip's id, for an owner that wants to
 * reference it from aria-describedby.
 */
import { useState, type ReactNode } from 'react'

type HelpTipProps = {
  label: string
  id: string
  children: ReactNode
  className?: string
}

export function HelpTip({ label, id, children, className }: HelpTipProps) {
  const [open, setOpen] = useState(false)
  return (
    <span
      className={className ? `field-help ${className}` : 'field-help'}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="field-help-btn"
        aria-label={`Help: ${label}`}
        aria-describedby={id}
        aria-expanded={open}
        // Open, never toggle: hover and focus have already opened it by the time the click arrives, so a
        // toggle would close it again on every mouse click and tap. Escape, blur and mouse-leave close it.
        onClick={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false)
        }}
      >
        i
      </button>
      <span id={id} role="tooltip" className="field-help-text" hidden={!open} onMouseDown={(e) => e.preventDefault()}>
        {children}
      </span>
    </span>
  )
}
