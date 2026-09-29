/*
 * ConfirmButton: a two-step destructive action with no typed word.
 *
 *   <ConfirmButton name="Preset A" busy={busy} onConfirm={() => deletePreset(id)} />
 *
 * First press: "Delete…" becomes "Confirm delete ‹name›" (danger) plus a
 * Cancel link, and "Press again to delete ‹name›" is announced (aria-live).
 * Second press runs onConfirm (the caller sends {confirm: true}). It reverts
 * after 5 s idle, on blur (leaving the pair), on Esc, on Cancel, and whenever
 * it becomes busy or disabled (re-enabling starts again from the first step).
 * After Cancel/Esc or a finished action (e.g. one that failed), focus goes
 * back to the first-step button.
 *
 * Props
 *   name          what is deleted, used in the labels
 *   onConfirm     the action; the caller shows its own result/error
 *   label         first-step text (default "Delete…")
 *   confirmLabel  second-step text (default "Confirm delete ‹name›")
 *   busy          the action is running: disabled, "Working…", aria-busy
 *   verb          what the action does, in the default confirm label and the
 *                 announcement (default "delete": "Confirm delete ‹name›")
 *   tone          second-step style: 'danger' (default) or 'primary' for a
 *                 non-destructive but overwriting action
 *   disabled      cannot run now (show the reason next to it)
 *   ariaLabel     first-step accessible name when "‹label› ‹name›" would repeat
 *                 itself (e.g. label "Show token…", name "extension token")
 *   describedBy   id of the line that says why it is disabled
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
  verb?: string
  tone?: 'danger' | 'primary'
  ariaLabel?: string
  describedBy?: string
}

export function ConfirmButton({ name, onConfirm, label = 'Delete…', confirmLabel, busy, disabled, verb = 'delete', tone = 'danger', ariaLabel, describedBy }: Props) {
  const blocked = !!(busy || disabled)
  const [armed, setArmed] = useState(false)
  // Becoming busy/disabled disarms (state adjusted during render, no effect).
  if (armed && blocked) setArmed(false)
  const live = armed && !blocked
  const wrapRef = useRef<HTMLSpanElement>(null)
  const firstRef = useRef<HTMLButtonElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const refocus = useRef(false)

  const on = (event: ConfirmEvent) => {
    const next = confirmStep(live, event, blocked)
    if (live && !next.armed && event !== 'blur' && event !== 'timeout') refocus.current = true
    setArmed(next.armed)
    if (next.run) onConfirm()
  }

  useEffect(() => {
    if (!live) return
    confirmRef.current?.focus()
    const timer = setTimeout(() => setArmed(false), CONFIRM_REVERT_MS)
    return () => clearTimeout(timer)
  }, [live])

  // Back to the first-step button once it can take focus again (not while busy).
  useEffect(() => {
    if (live || blocked || !refocus.current) return
    refocus.current = false
    firstRef.current?.focus()
  }, [live, blocked])

  return (
    <span
      ref={wrapRef}
      className="confirm-button"
      aria-busy={busy || undefined}
      onKeyDown={(e) => {
        if (live && e.key === 'Escape') {
          e.stopPropagation()
          on('escape')
        }
      }}
      onBlur={(e) => {
        if (live && !wrapRef.current?.contains(e.relatedTarget as Node | null)) on('blur')
      }}
    >
      {live ? (
        <>
          <button ref={confirmRef} type="button" className={tone} onClick={() => on('press')}>
            {confirmLabel ?? confirmLabelFor(name, verb)}
          </button>
          <button type="button" className="link" onClick={() => on('cancel')}>
            Cancel
          </button>
        </>
      ) : (
        <button
          ref={firstRef}
          type="button"
          disabled={blocked}
          aria-label={ariaLabel ?? `${label.replace(/…$/, '')} ${name}`}
          aria-describedby={describedBy}
          onClick={() => on('press')}
        >
          {busy ? 'Working…' : label}
        </button>
      )}
      <span className="visually-hidden" aria-live="polite">
        {live ? armedAnnouncement(name, verb) : ''}
      </span>
    </span>
  )
}
