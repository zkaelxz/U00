import { useCallback, useRef, useState, type ReactNode } from 'react'

import { usePopoverDismiss } from '../hooks/usePopoverDismiss'
import { Sheet } from './Sheet'

type Props = {
  // Names the button, the popover and the phone Sheet.
  label: string
  phone: boolean
  // Phone only: content above the form (the Reader's "Go to" and metrics).
  sheetExtra?: ReactNode
  // Close the popover when the window loses focus: a click into an iframe never reaches this document.
  closeOnWindowBlur?: boolean
  children: ReactNode
}

/** The "Aa" settings button: a popover on desktop, a bottom Sheet on phones. */
export function AaControl({ label, phone, sheetExtra, closeOnWindowBlur = false, children }: Props) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  const close = useCallback(() => setOpen(false), [])
  usePopoverDismiss(open && !phone, wrap, buttonRef, close, closeOnWindowBlur)

  const button = (
    <button
      ref={buttonRef}
      type="button"
      className="reader-aa"
      aria-label={label}
      aria-expanded={open}
      onClick={() => setOpen((v) => !v)}
    >
      Aa
    </button>
  )
  if (phone) {
    return (
      <>
        {button}
        <Sheet open={open} title={label} onClose={close}>
          {sheetExtra}
          {children}
        </Sheet>
      </>
    )
  }
  return (
    <div className="reader-aa-wrap" ref={wrap}>
      {button}
      {open && <div className="reader-popover" role="dialog" aria-label={label}>{children}</div>}
    </div>
  )
}
