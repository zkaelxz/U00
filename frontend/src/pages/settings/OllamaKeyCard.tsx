/*
 * Settings > Your Ollama key: a signed-in member saves their own Ollama API
 * key, so their cloud-model runs use their Ollama account and free allowance
 * rather than the main PC's. The key is typed into a password field, sent
 * once and cleared; the server keeps it encrypted and only ever answers with
 * its last four characters. Removing it takes a confirm step.
 */
import { useCallback, useEffect, useState } from 'react'

import { getMyOllamaKey, removeMyOllamaKey, saveMyOllamaKey } from '../../api/memberKey'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { recheckSession, useSession } from '../../hooks/useSession'
import type { MemberKeyStatus } from '../../types/memberKey'
import {
  MAX_KEY_CHARS, OLLAMA_KEY_NOTE, OLLAMA_KEY_TITLE, OLLAMA_KEYS_URL, keyProblem, keyStateText, showOllamaKey,
} from './ollamaKeyModel'

export function OllamaKeyCard() {
  const session = useSession()
  if (!showOllamaKey(session)) return null
  return (
    <Card title={OLLAMA_KEY_TITLE} aria-label={OLLAMA_KEY_TITLE}>
      <OllamaKeyBody />
    </Card>
  )
}

function OllamaKeyBody() {
  const [status, setStatus] = useState<MemberKeyStatus | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<'save' | 'remove' | null>(null)
  const [value, setValue] = useState('')
  const [touched, setTouched] = useState(false)
  const [note, setNote] = useState('')
  const [nowMs, setNowMs] = useState(() => Date.now())

  const load = useCallback(() => {
    getMyOllamaKey().then((r) => {
      setNowMs(Date.now())
      setStatus(r)
    }, setError)
  }, [])
  useEffect(load, [load])

  const problem = keyProblem(value)
  const save = async () => {
    setTouched(true)
    if (problem) return
    setBusy('save')
    setNote('')
    setError(null)
    try {
      setStatus(await saveMyOllamaKey(value.trim()))
      setNowMs(Date.now())
      setValue('')
      setTouched(false)
      setNote('Saved. Your next cloud-model run uses your account.')
      // The pickers read whether cloud models are open to you from the session.
      void recheckSession()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  const remove = async () => {
    setBusy('remove')
    setNote('')
    setError(null)
    try {
      setStatus(await removeMyOllamaKey())
      setNote('Removed.')
      void recheckSession()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  const saved = !!status && (status.has_key || status.unreadable)
  return (
    <div className="settings-group" data-testid="ollama-key">
      <p className="settings-note">
        {OLLAMA_KEY_NOTE}{' '}
        <a href={OLLAMA_KEYS_URL} target="_blank" rel="noreferrer noopener">Get a key at ollama.com</a>
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {status === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="status-row">
          <span className="status-row-name">{keyStateText(status, nowMs)}</span>
          {status.has_key && <Badge tone="ok">Saved</Badge>}
          {status.unreadable && <Badge tone="warn">Paste again</Badge>}
        </div>
      )}
      <form className="diag-stack" onSubmit={(e) => { e.preventDefault(); void save() }}>
        <Field label={saved ? 'Replace with a new key' : 'Ollama API key'} error={touched ? problem : null}>
          <input type="password" value={value} maxLength={MAX_KEY_CHARS} autoComplete="off" spellCheck={false}
            onChange={(e) => setValue(e.target.value)} />
        </Field>
        <div className="settings-actions">
          <button type="submit" className={buttonClass('primary')} disabled={busy !== null}
            aria-busy={busy === 'save'}>
            {busy === 'save' ? 'Saving…' : 'Save key'}
          </button>
          {saved && (
            <ConfirmButton name="your Ollama key" label="Remove key…" ariaLabel="Remove your Ollama key"
              verb="remove" busy={busy === 'remove'}
              disabled={busy !== null} onConfirm={() => void remove()} />
          )}
        </div>
      </form>
      <span className="muted" aria-live="polite">{note}</span>
    </div>
  )
}
