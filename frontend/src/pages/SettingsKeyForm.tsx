import { useReducer } from 'react'
import { clearEngineKey, setEngineKey } from '../api/settings'
import { Field } from '../components/Field'
import { ConfirmButton } from '../components/ConfirmButton'
import { buttonClass } from '../components/uiClasses'
import type { EngineKeyResult } from '../types/settings'
import { balanceLinkFor, keyLinkFor } from './settings/providerBalanceLinks'
import { SAVED_ON_PC_NOTE } from './settings/preferences'
import { initialKeyForm, keyFormReducer } from './settingsKeys'

// Speaker detection (pyannote) downloads gated models, so the token alone is not enough.
const HF_TOKEN_HELP =
  'Get the token at huggingface.co/settings/tokens (read access is enough), then accept the terms of the ' +
  'pyannote speaker models (speaker-diarization-community-1 and speaker-diarization-3.1) on their Hugging Face pages.'

type Props = {
  engine: string
  label: string
  configured: boolean
  onResult: (r: EngineKeyResult) => void
  // Runs the engine's existing test; offered as "Test key" after a save.
  onTest?: () => void
  testing?: boolean
}

// One secret engine: a password input that is never pre-filled, an explicit
// Save, and a two-step Clear. The row around it shows
// only set/missing; the stored key is never read back.
export function SettingsKeyForm({ engine, label, configured, onResult, onTest, testing = false }: Props) {
  const [s, dispatch] = useReducer(keyFormReducer, initialKeyForm)
  const balanceUrl = balanceLinkFor(engine)
  const keyUrl = keyLinkFor(engine)

  async function send(call: () => Promise<EngineKeyResult>, notice: (r: EngineKeyResult) => string, saved = false) {
    dispatch({ type: 'send' })
    try {
      const r = await call()
      onResult(r)
      dispatch({ type: 'done', notice: notice(r), saved: saved && r.configured })
    } catch (e) {
      dispatch({ type: 'failed', error: e })
    }
  }

  function save() {
    const value = s.draft
    send(
      () => setEngineKey(engine, value),
      (r) => (r.configured ? 'Saved.' : 'Saved, but the key is not being picked up.'),
      true,
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
        help={engine === 'hf_token' ? `${HF_TOKEN_HELP} ${SAVED_ON_PC_NOTE}` : SAVED_ON_PC_NOTE}
        error={s.error}
      >
        <input
          type="password"
          autoComplete="new-password"
          data-1p-ignore
          spellCheck={false}
          value={s.draft}
          placeholder={configured ? 'Type a new key to replace the saved one' : 'Paste the key'}
          disabled={s.busy}
          autoFocus
          onChange={(e) => dispatch({ type: 'edit', value: e.target.value })}
        />
      </Field>
      <div className="settings-actions">
        <button
          type="button"
          className={buttonClass('primary')}
          disabled={s.busy || !s.draft.trim()}
          onClick={save}
        >
          Save key
        </button>
        {s.saved && onTest && (
          <button type="button" className={buttonClass('secondary')} disabled={testing} onClick={onTest}>
            Test key
          </button>
        )}
        <ConfirmButton name={`${label} key`} label="Clear key" ariaLabel={`Clear ${label} key`} verb="remove" disabled={s.busy || !configured} onConfirm={clear} />
        {keyUrl && (
          <a className="button-link" href={keyUrl} target="_blank" rel="noreferrer noopener">
            Get a key
          </a>
        )}
        {balanceUrl && (
          <a className="button-link" href={balanceUrl} target="_blank" rel="noopener noreferrer">
            Check {label} balance
          </a>
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
