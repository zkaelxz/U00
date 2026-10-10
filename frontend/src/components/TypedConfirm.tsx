/*
 * TypedConfirm: a destructive action that runs only after the person types a
 * word (e.g. "resegment", "restore").
 *
 *   <TypedConfirm word="restore" action="Restore snapshot" busy={busy}
 *                 onConfirm={run} onCancel={close}>
 *     Your current lines are saved as a snapshot first.
 *   </TypedConfirm>
 */
import { useId, useState, type ReactNode } from 'react'

import { typedMatches } from './typedConfirmMatch'
import './typedConfirm.css'
import { buttonClass } from './uiClasses'

type TypedConfirmProps = {
  word: string
  // Letter case must match (default false: case-insensitive).
  exact?: boolean
  // Focus the word input when it appears (e.g. opened from a menu).
  autoFocus?: boolean
  action: string
  busy?: boolean
  // A reason the action cannot run right now; shown and the button disabled.
  blocked?: ReactNode
  onConfirm: () => void
  onCancel?: () => void
  children?: ReactNode
}

export function TypedConfirm({ word, exact, autoFocus, action, busy, blocked, onConfirm, onCancel, children }: TypedConfirmProps) {
  const [typed, setTyped] = useState('')
  const id = useId()
  const ready = typedMatches(typed, word, exact) && !busy && !blocked
  return (
    <form
      className="delete-confirm"
      onSubmit={(e) => {
        e.preventDefault()
        if (ready) onConfirm()
      }}
    >
      {children}
      {/* Same look as Field; the word is code-styled so it reads as something to type. */}
      <div className="field-item">
        <div className="field-label-row">
          <label htmlFor={id}>
            Type <code className="typed-word">{word}</code> to confirm
          </label>
        </div>
        <div className="field-control">
          <input
            id={id}
            value={typed}
            autoComplete="off"
            autoFocus={autoFocus}
            // An all-capitals word (DELETE) gets a capitals keyboard on phones.
            autoCapitalize={word === word.toUpperCase() && word !== word.toLowerCase() ? 'characters' : 'none'}
            spellCheck={false}
            onChange={(e) => setTyped(e.target.value)}
          />
        </div>
      </div>
      {blocked && <p className="muted">{blocked}</p>}
      <div className="actions">
        <button type="submit" className="danger" disabled={!ready}>
          {busy ? 'Working…' : action}
        </button>
        {onCancel && (
          <button type="button" className={buttonClass('ghost')} onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
