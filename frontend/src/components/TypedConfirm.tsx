/*
 * TypedConfirm: a destructive action that runs only after the person types a
 * word (e.g. "resegment", "restore").
 *
 *   <TypedConfirm word="restore" action="Restore snapshot" busy={busy}
 *                 onConfirm={run} onCancel={close}>
 *     Your current lines are saved as a snapshot first.
 *   </TypedConfirm>
 */
import { useState, type ReactNode } from 'react'

import { Field } from './Field'
import { typedMatches } from './typedConfirm'

type TypedConfirmProps = {
  word: string
  action: string
  busy?: boolean
  // A reason the action cannot run right now; shown and the button disabled.
  blocked?: string | null
  onConfirm: () => void
  onCancel?: () => void
  children?: ReactNode
}

export function TypedConfirm({ word, action, busy, blocked, onConfirm, onCancel, children }: TypedConfirmProps) {
  const [typed, setTyped] = useState('')
  const ready = typedMatches(typed, word) && !busy && !blocked
  return (
    <form
      className="delete-confirm"
      onSubmit={(e) => {
        e.preventDefault()
        if (ready) onConfirm()
      }}
    >
      {children}
      <Field label={`Type ${word} to confirm`}>
        <input
          value={typed}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          onChange={(e) => setTyped(e.target.value)}
        />
      </Field>
      {blocked && <p className="muted">{blocked}</p>}
      <div className="actions">
        <button type="submit" className="danger" disabled={!ready}>
          {busy ? 'Working…' : action}
        </button>
        {onCancel && (
          <button type="button" className="link" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
