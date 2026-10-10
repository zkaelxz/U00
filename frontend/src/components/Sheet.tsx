/*
 * Sheet: a modal <dialog>. A bottom sheet on phones (≤ 640px), a centred
 * dialog on wider screens.
 *
 *   <Sheet open={open} title="Line #5" onClose={() => setOpen(false)}>...</Sheet>
 *
 * Esc, a click on the backdrop and the "Close" button all call onClose. The
 * browser keeps focus inside while it is open and returns it on close.
 */
import { useId, type ReactNode } from 'react'

import { useModalDialog } from '../hooks/useModalDialog'
import './sheet.css'

type SheetProps = {
  open: boolean
  title: string
  onClose: () => void
  children: ReactNode
}

export function Sheet({ open, title, onClose, children }: SheetProps) {
  const ref = useModalDialog(open)
  const titleId = useId()

  return (
    <dialog
      ref={ref}
      className="sheet"
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      {open && (
        <div className="sheet-body">
          <div className="sheet-head">
            <h3 id={titleId}>{title}</h3>
            <button type="button" className="sheet-close" aria-label="Close" onClick={onClose}>
              ×
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  )
}
