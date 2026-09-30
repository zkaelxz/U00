/*
 * Settings > Developer Mode (Step 42): shows the AI maintenance assistant in
 * the menu. PC only: from another device the settings route answers 403 and
 * this card is not rendered.
 */
import { useEffect, useState } from 'react'

import { getAssistantSettings, saveAssistantSettings } from '../../api/assistant'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import type { AssistantSettings } from '../../types/assistant'
import { CloudConsent } from '../assistant/CloudConsent'
import { announceDeveloperMode } from '../assistant/developerMode'
import { DEVELOPER_MODE_HELP, assistantErrorText, isForbidden } from '../assistant/assistantFormat'

const TITLE = 'Developer Mode'

export function DeveloperModeCard() {
  // null: loading; 'hidden': another device.
  const [on, setOn] = useState<boolean | null | 'hidden'>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [settings, setSettings] = useState<AssistantSettings | null>(null)

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live) return
      if (getPcMode() === 'remote') return setOn('hidden')
      getAssistantSettings().then(
        (s) => {
          if (!live) return
          setOn(s.developer_mode)
          setSettings(s)
        },
        (e: unknown) => {
          if (!live) return
          if (isForbidden(e)) setOn('hidden')
          else setError(assistantErrorText(e))
        },
      )
    })
    return () => {
      live = false
    }
  }, [])

  if (on === 'hidden') return null

  const flip = (next: boolean) => {
    setBusy(true)
    setError(null)
    saveAssistantSettings({ developer_mode: next }).then(
      (s) => {
        setOn(s.developer_mode)
        setSettings(s)
        setBusy(false)
        announceDeveloperMode(s.developer_mode)
      },
      (e: unknown) => {
        if (isForbidden(e)) setOn('hidden')
        else setError(assistantErrorText(e))
        setBusy(false)
      },
    )
  }

  return (
    <Card title={TITLE} aria-label={TITLE}>
      {on === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="setting-list">
          <Field label="Developer Mode" help={DEVELOPER_MODE_HELP}>
            <Toggle checked={on} disabled={busy} onChange={flip} />
          </Field>
        </div>
      )}
      {on === true && settings && <CloudConsent settings={settings} onSettings={setSettings} />}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Card>
  )
}
