// The error line under one Reader action, with "Try again" after a 429.
import type { Action } from './useReaderAction'
import { readerErrorText } from './readerErrors'

type ActionState = Pick<Action<unknown[]>, 'error' | 'busy' | 'retry'>

export function ActionError({ action, paidEngine }: { action: ActionState; paidEngine?: boolean }) {
  if (!action.error) return null
  const { title, detail, retry } = readerErrorText(action.error, { paidEngine })
  return (
    <div className="reader-error" role="alert">
      <span className="error">{title}</span>
      {detail && <span className="muted"> {detail}</span>}
      {retry && (
        <button type="button" onClick={action.retry} disabled={action.busy}>
          Try again
        </button>
      )}
    </div>
  )
}
