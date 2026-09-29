import { useReducer } from 'react'
import { clearEngineKey, setEngineKey } from '../api/settings'
import { Field } from '../components/Field'
import { buttonClass } from '../components/uiClasses'
import type { EngineKeyResult } from '../types/settings'
import { initialKeyForm, keyFormReducer } from './settingsKeys'

type Props = {
  engine: string
  label: string
  configured: boolean
  onResult: (r: EngineKeyResult) => void
}

// One secret engine: a password input that is never pre-filled, an explicit
// Save with a confirm step, and a two-step Clear. The row around it shows
// only set/missing; the stored key is never read back.
export function SettingsKeyForm({ engine, label, configured, onResult }: Props) {
  const [s, dispatch] = useReducer(keyFormReducer, initialKeyForm)

  async function send(call: () => Promise<EngineKeyResult>, notice: (r: EngineKeyResult) => string) {
    dispatch({ type: 'send' })
    try {
      const r = await call()
      onResult(r)
      dispatch({ type: 'done', notice: notice(r) })
    } catch (e) {
      dispatch({ type: 'failed', error: e })
    }
  }

  function save() {
    const value = s.draft
    send(
      () => setEngineKey(engine, value),
      (r) => (r.configured ? 'Saved.' : 'Saved, but the key is not being picked up.'),
    )
  }

  function clear() {
    send(
      () => clearEngineKey(engine),
      (r) => (r.configured ? 'Removed from .env, but a system environment variable still sets it.' : 'Cleared.'),
    )
  }

  return (
    <div className="status-form">
      <Field
        label={`${label} key`}
        help="Saved to .env on the Baihe PC. The saved key is never shown again."
        error={s.error}
      >
        <input
          type="password"
          autoComplete="off"
          spellCheck={false}
          value={s.draft}
          placeholder={configured ? 'Type a new key to replace the saved one' : 'Paste the key'}
          disabled={s.busy}
          autoFocus
          onChange={(e) => dispatch({ type: 'edit', value: e.target.value })}
        />
      </Field>
      <div className="settings-actions">
        {s.confirm === 'save' ? (
          <>
            <button type="button" className={buttonClass('primary')} disabled={s.busy} onClick={save}>
              Confirm: save {label} key
            </button>
            <button type="button" className={buttonClass('ghost')} onClick={() => dispatch({ type: 'cancel' })}>
              Cancel
            </button>
          </>
        ) : s.confirm === 'clear' ? (
          <>
            <button type="button" className={buttonClass('danger')} disabled={s.busy} onClick={clear}>
              Confirm: remove {label} key
            </button>
            <button type="button" className={buttonClass('ghost')} onClick={() => dispatch({ type: 'cancel' })}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className={buttonClass('primary')}
              disabled={s.busy || !s.draft.trim()}
              onClick={() => dispatch({ type: 'askSave' })}
            >
              Save key
            </button>
            <button
              type="button"
              className={buttonClass('ghost')}
              disabled={s.busy || !configured}
              onClick={() => dispatch({ type: 'askClear' })}
            >
              Clear key
            </button>
          </>
        )}
      </div>
      {s.notice && (
        <p className="muted" role="status">
          {s.notice}
        </p>
      )}
    </div>
  )
}
