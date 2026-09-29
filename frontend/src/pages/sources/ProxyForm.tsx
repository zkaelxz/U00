import { useState } from 'react'

import { setSourcesProxy } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import type { SourcesSettings } from '../../types/sources'
import { proxyProblem } from './sourcesFormat'

type Props = {
  settings: SourcesSettings
  onSaved: (s: SourcesSettings) => void
}

const HELP =
  'Every source request goes through this HTTP(S) proxy when set. The address is never shown again once saved, since it can hold a password.'

/** The source proxy (PC only). Write-only: the page only knows whether one is set. */
export function ProxyForm({ settings, onSaved }: Props) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [note, setNote] = useState('')
  const problem = proxyProblem(value)

  async function save(url: string) {
    setError(null)
    setNote('')
    setBusy(true)
    try {
      const next = await setSourcesProxy(url)
      setValue('')
      setNote(next.proxy_configured ? 'Proxy saved.' : 'Proxy cleared.')
      onSaved(next)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sources-proxy" data-testid="sources-proxy">
      <Field label={`Proxy (${settings.proxy_configured ? 'set' : 'none'})`} help={HELP} error={problem ?? undefined}>
        <input
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder={settings.proxy_configured ? 'Type a new address to replace it' : 'http://127.0.0.1:8080'}
          value={value}
          maxLength={500}
          onChange={(e) => {
            setNote('')
            setValue(e.target.value)
          }}
        />
      </Field>
      <div className="actions">
        <button type="button" disabled={busy || !value.trim() || !!problem} onClick={() => save(value.trim())}>
          {busy ? 'Saving…' : 'Save proxy'}
        </button>
        {settings.proxy_configured && (
          <button type="button" className="link" disabled={busy} onClick={() => save('')}>
            Clear proxy
          </button>
        )}
        <span className="muted" aria-live="polite">
          {note}
        </span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
    </div>
  )
}
