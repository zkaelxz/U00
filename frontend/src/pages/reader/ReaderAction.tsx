// Small shared pieces of the Reader's actions: the error line under one action, with "Try again" after a 429, and the link to Translate's editors.
import { useSession } from '../../hooks/useSession'
import { routeHref, type DramaFocus } from '../../router'
import { holds } from '../diagnostics/adminUsers'
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

// The Reader's glossary and characters are read-only copies of what Translate edits. The server
// still checks lines.edit on every write; this only hides a link that would lead to a dead end.
export function EditInTranslate({ dramaId, focus }: { dramaId: number; focus: DramaFocus }) {
  const session = useSession()
  if (!holds(session, 'lines.edit')) return null
  return (
    <p>
      <a href={routeHref({ name: 'drama', id: dramaId, stage: 'translate', focus })}>Edit in Translate</a>
    </p>
  )
}
