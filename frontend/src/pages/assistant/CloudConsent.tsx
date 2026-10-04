// Per-provider consent to send this app's code and logs to a cloud engine
// The server refuses a cloud engine without it
// (409); Ollama runs on this PC and needs none.
import { useState } from 'react'

import { saveAssistantSettings } from '../../api/assistant'
import { Field } from '../../components/Field'
import { humanize } from '../../components/labels'
import { Toggle } from '../../components/Toggle'
import type { AssistantSettings } from '../../types/assistant'
import { assistantErrorText } from './assistantFormat'

type Props = {
  settings: AssistantSettings
  onSettings: (s: AssistantSettings) => void
  // Only these engines (e.g. the one picked on the Assistant page); default: every cloud engine.
  only?: string[]
}

const CONSENT_HELP =
  'The assistant reads this app’s source code and its (redacted) logs to answer. With this on, that text is sent to this provider. Ollama keeps everything on this PC.'

export function CloudConsent({ settings, onSettings, only }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const consent = settings.cloud_consent ?? {}
  const engines = Object.keys(consent).filter((e) => !only || only.includes(e))
  if (engines.length === 0) return null

  const flip = (engine: string, next: boolean) => {
    setBusy(true)
    setError(null)
    saveAssistantSettings({ cloud_consent: { [engine]: next } }).then(
      (s) => {
        setBusy(false)
        onSettings(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(assistantErrorText(e))
      },
    )
  }

  return (
    <div className="setting-list" data-testid="cloud-consent">
      {engines.map((e) => (
        <Field key={e} label={`Send code and logs to ${humanize('engine', e)}`} help={CONSENT_HELP}>
          <Toggle checked={consent[e] === true} disabled={busy} onChange={(next) => flip(e, next)} />
        </Field>
      ))}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
