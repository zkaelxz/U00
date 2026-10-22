/*
 * Settings > Developer Mode: shows the AI maintenance assistant in
 * the menu, and holds the switch that lets an admin upload from away
 * (it turns itself off after 7 days, and with Developer Mode). PC only: from
 * another device the settings route answers 403 and this card is not rendered.
 */
import { useEffect, useState } from 'react'

import { getAssistantSettings, saveAssistantSettings } from '../../api/assistant'
import { getPcMode, loadPcMode, pcOnlyFetch } from '../../api/pcOnly'
import { getSettings, updateSetting } from '../../api/settings'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import type { AssistantSettings } from '../../types/assistant'
import { CloudConsent } from '../assistant/CloudConsent'
import { announceDeveloperMode } from '../assistant/developerMode'
import { DEVELOPER_MODE_HELP, assistantErrorText, isAssistantForbidden } from '../assistant/assistantFormat'

const TITLE = 'Developer Mode'

export function DeveloperModeCard() {
  // null: loading; 'hidden': another device.
  const [on, setOn] = useState<boolean | null | 'hidden'>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [settings, setSettings] = useState<AssistantSettings | null>(null)
  const [remoteUploads, setRemoteUploads] = useState<boolean | null>(null)

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
          if (isAssistantForbidden(e)) setOn('hidden')
          else setError(assistantErrorText(e))
        },
      )
    })
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    if (on !== true) return
    let live = true
    getSettings().then(
      (s) => live && setRemoteUploads(s.remote_uploads),
      (e: unknown) => live && setError(assistantErrorText(e)),
    )
    return () => {
      live = false
    }
  }, [on])

  if (on === 'hidden') return null

  const flipRemoteUploads = (next: boolean) => {
    setBusy(true)
    setError(null)
    updateSetting('remote_uploads', next, pcOnlyFetch()).then(
      (s) => {
        setRemoteUploads(s.remote_uploads)
        setBusy(false)
      },
      (e: unknown) => {
        setError(assistantErrorText(e))
        setBusy(false)
      },
    )
  }

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
        if (isAssistantForbidden(e)) setOn('hidden')
        else setError(assistantErrorText(e))
        setBusy(false)
      },
    )
  }

  return (
    <Card title={TITLE} aria-label={TITLE} meta={typeof on === 'boolean' ? (on ? 'On' : 'Off') : undefined}>
      {on === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="setting-list">
          <Field label="Developer Mode" help={DEVELOPER_MODE_HELP}>
            <Toggle checked={on} disabled={busy} onChange={flip} />
          </Field>
          {on && remoteUploads !== null && (
            <Field label="Allow uploads from away">
              <Toggle checked={remoteUploads} disabled={busy} onChange={flipRemoteUploads} />
            </Field>
          )}
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
