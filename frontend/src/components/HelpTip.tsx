/*
 * HelpTip: the focusable (i) button and its tooltip, shown on hover, focus or click and hidden with
 * Escape, blur, a second tap or a press outside it. Field uses it for a control's help; use it directly for a heading or a block.
 *
 *   <HelpTip label="Golden sets" id={helpId}>Steps…</HelpTip>
 *
 * `label` names the button ("Help: <label>"); `id` is the tooltip's id, for an owner that wants to
 * reference it from aria-describedby.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { helpOpenAfterClick } from './fieldIds'

type HelpTipProps = {
  label: string
  id: string
  children: ReactNode
  className?: string
}

export function HelpTip({ label, id, children, className }: HelpTipProps) {
  const [open, setOpenState] = useState(false)
  const wrapRef = useRef<HTMLSpanElement>(null)
  const openAtPress = useRef<boolean | null>(null)
  // Hover events render at a lower priority than clicks, so the state captured
  // by a handler can lag the DOM; the ref is always current.
  const openNow = useRef(false)
  const setOpen = (value: boolean) => {
    openNow.current = value
    setOpenState(value)
  }

  // iOS Safari neither focuses a tapped button nor leaves it for a tap on
  // empty space, so blur and pointer-leave cannot be relied on to close it.
  useEffect(() => {
    if (!open) return
    const closeOutside = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpenState(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [open])

  return (
    <span
      ref={wrapRef}
      className={className ? `field-help ${className}` : 'field-help'}
      // Pointer events, skipping touch: a tap's emulated mouseenter and mouseleave bracket the click,
      // so mouse events would open the tip and then close it again.
      onPointerEnter={(e) => e.pointerType !== 'touch' && setOpen(true)}
      onPointerLeave={(e) => e.pointerType !== 'touch' && setOpen(false)}
    >
      <button
        type="button"
        className="field-help-btn"
        aria-label={`Help: ${label}`}
        aria-describedby={id}
        aria-expanded={open}
        // A mouse or pen click on a hovered tip keeps it open (hover already opened it). Touch and
        // keyboard toggle, from the state the press found: a tap has already opened it (emulated hover,
        // then focus) by the time the click arrives.
        onPointerDown={(e) => {
          openAtPress.current = e.pointerType === 'touch' ? openNow.current : false
        }}
        onClick={() => {
          setOpen(helpOpenAfterClick(openAtPress.current, openNow.current))
          openAtPress.current = null
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          openAtPress.current = null
          setOpen(false)
        }}
        onKeyDown={(e) => {
          openAtPress.current = null
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
