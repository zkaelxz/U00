/*
 * ConfirmButton: a two-step destructive action with no typed word.
 *
 *   <ConfirmButton name="Preset A" busy={busy} onConfirm={() => deletePreset(id)} />
 *
 * First press: "Delete…" becomes "Confirm delete ‹name›" (danger) plus a
 * Cancel link, and "Press again to delete ‹name›" is announced (aria-live).
 * Second press runs onConfirm (the caller sends {confirm: true}). It reverts
 * after 5 s idle, on blur (leaving the pair), on Esc, or on Cancel.
 *
 * Props
 *   name          what is deleted, used in the labels
 *   onConfirm     the action; the caller shows its own result/error
 *   label         first-step text (default "Delete…")
 *   confirmLabel  second-step text (default "Confirm delete ‹name›")
 *   busy          the action is running: disabled, "Working…"
 *   disabled      cannot run now (show the reason next to it)
 *
 * Layout: inline on desktop; on phones (.confirm-button, index.css) it takes
 * its own line, right-aligned, with 44 px targets. Hide it entirely in
 * remote mode (usePcOnly) and show a muted note instead.
 */
import { useEffect, useRef, useState } from 'react'

import { CONFIRM_REVERT_MS, armedAnnouncement, confirmLabelFor, confirmStep, type ConfirmEvent } from './confirmButton'

type Props = {
  name: string
  onConfirm: () => void
  label?: string
  confirmLabel?: string
  busy?: boolean
  disabled?: boolean
}

export function ConfirmButton({ name, onConfirm, label = 'Delete…', confirmLabel, busy, disabled }: Props) {
  const [armed, setArmed] = useState(false)
  const wrapRef = useRef<HTMLSpanElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)

  const on = (event: ConfirmEvent) => {
    const next = confirmStep(armed, event)
    setArmed(next.armed)
    if (next.run) onConfirm()
  }

  useEffect(() => {
    if (!armed) return
    confirmRef.current?.focus()
    const timer = setTimeout(() => setArmed(false), CONFIRM_REVERT_MS)
    return () => clearTimeout(timer)
  }, [armed])

  return (
    <span
      ref={wrapRef}
      className="confirm-button"
      onKeyDown={(e) => {
        if (armed && e.key === 'Escape') {
          e.stopPropagation()
          on('escape')
        }
      }}
      onBlur={(e) => {
        if (armed && !wrapRef.current?.contains(e.relatedTarget as Node | null)) on('blur')
      }}
    >
      {armed ? (
        <>
          <button ref={confirmRef} type="button" className="danger" disabled={busy || disabled} onClick={() => on('press')}>
            {confirmLabel ?? confirmLabelFor(name)}
          </button>
          <button type="button" className="link" onClick={() => on('cancel')}>
            Cancel
          </button>
        </>
      ) : (
        <button
          type="button"
          disabled={busy || disabled}
          aria-label={`${label.replace(/…$/, '')} ${name}`}
          onClick={() => on('press')}
        >
          {busy ? 'Working…' : label}
        </button>
      )}
      <span className="visually-hidden" aria-live="polite">
        {armed ? armedAnnouncement(name) : ''}
      </span>
    </span>
  )
}
