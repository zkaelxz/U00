import { useState } from 'react'

import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import { canAlways, type SameFixOffer } from './sameFix'

interface Props {
  offer: SameFixOffer
  busy: boolean
  onApply: () => void
  onAlways: () => void
  onDismiss: () => void
}

export function SameFixNotice({ offer, busy, onApply, onAlways, onDismiss }: Props) {
  const [open, setOpen] = useState(false)
  const n = offer.matches.length
  return (
    <div className="review-undo" role="status" data-testid="same-fix">
      <span className="review-undo-text">
        {offer.rest ? `Same fix still to apply in ${n} line${n === 1 ? '' : 's'}: ` : `Same fix in ${n} other line${n === 1 ? '' : 's'}? `}
        <del>{offer.fix.find}</del> → <ins>{offer.fix.replace}</ins>{' '}
        <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Hide lines' : 'Show lines'}
        </button>
      </span>
      <span className="review-undo-actions">
        <button type="button" className={buttonClass('primary')} disabled={busy} onClick={onApply}>
          {offer.rest ? 'Apply the rest' : 'Apply'}
        </button>
        {canAlways(offer.fix) && !offer.rest && (
          <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={onAlways}
            title="Also save it as a word replacement, so new translations in this series get it">
            Apply and always
          </button>
        )}
        <button type="button" className={buttonClass('secondary')} aria-label="Dismiss" onClick={onDismiss}>×</button>
      </span>
      {open && (
        <ul className="review-matches" data-testid="same-fix-lines">
          {offer.matches.map((m) => (
            <li key={m.id}>
              <span className="muted">#{lineNumber(m.idx)}</span> <del>{m.old_text}</del> <ins>{m.new_text}</ins>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
