import { useReducer } from 'react'
import { clearEngineKey, setEngineKey } from '../api/settings'
import { Field } from '../components/Field'
import type { EngineKeyResult } from '../types/settings'
import { initialKeyForm, keyFormReducer } from './settingsKeys'

type Props = {
  engine: string
  label: string
  configured: boolean
  onResult: (r: EngineKeyResult) => void
}

const rowStyle = { display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap', alignItems: 'center' } as const

// One secret engine: a password input that is never pre-filled, an explicit
// Save with a confirm step, and a two-step Clear. Shows only configured
// yes/no; the stored key is never read back.
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
    <div style={{ display: 'grid', gap: 'var(--space-1)' }}>
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
          placeholder={configured ? 'Configured (type a new key to replace it)' : 'Not configured'}
          disabled={s.busy}
          onChange={(e) => dispatch({ type: 'edit', value: e.target.value })}
        />
      </Field>
      <div style={rowStyle}>
        <span className="muted" data-testid={`key-${engine}`}>
          {configured ? 'Configured: yes' : 'Configured: no'}
        </span>
        {s.confirm === 'save' ? (
          <>
            <button type="button" className="primary" disabled={s.busy} onClick={save}>
              Confirm: save {label} key
            </button>
            <button type="button" onClick={() => dispatch({ type: 'cancel' })}>
              Cancel
            </button>
          </>
        ) : s.confirm === 'clear' ? (
          <>
            <button type="button" className="danger" disabled={s.busy} onClick={clear}>
              Confirm: remove {label} key
            </button>
            <button type="button" onClick={() => dispatch({ type: 'cancel' })}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              disabled={s.busy || !s.draft.trim()}
              onClick={() => dispatch({ type: 'askSave' })}
            >
              Save key
            </button>
            <button
              type="button"
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
