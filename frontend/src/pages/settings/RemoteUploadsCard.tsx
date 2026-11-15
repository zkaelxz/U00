/*
 * People and access > Uploads from away: the PC's switch that lets an admin upload from another device. It
 * lapses after 7 days and needs Developer Mode (services/settings_service.remote_uploads_on); with the mode
 * off it is shown but cannot be turned on, and the card says where to turn the mode on. PC only.
 */
import { useEffect, useState } from 'react'

import { getAssistantSettings } from '../../api/assistant'
import { getPcMode, loadPcMode, pcOnlyFetch } from '../../api/pcOnly'
import { getSettings, updateSetting } from '../../api/settings'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { usePcOnly } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import { assistantErrorText } from '../assistant/assistantFormat'

export const REMOTE_UPLOADS_TITLE = 'Uploads from away'

export function RemoteUploadsCard() {
  if (usePcOnly() === 'remote') return null
  return <RemoteUploadsControls />
}

function RemoteUploadsControls() {
  const [developerMode, setDeveloperMode] = useState<boolean | null>(null)
  const [uploads, setUploads] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Another device, or an older server: nothing to offer.
  const [hidden, setHidden] = useState(false)

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return setHidden(true)
      getAssistantSettings().then(
        (s) => live && setDeveloperMode(s.developer_mode),
        () => live && setHidden(true),
      )
      getSettings().then(
        (s) => live && setUploads(s.remote_uploads),
        (e: unknown) => live && setError(assistantErrorText(e)),
      )
    })
    return () => {
      live = false
    }
  }, [])

  if (hidden) return null

  const flip = (next: boolean) => {
    setBusy(true)
    setError(null)
    updateSetting('remote_uploads', next, pcOnlyFetch()).then(
      (s) => {
        setUploads(s.remote_uploads)
        setBusy(false)
      },
      (e: unknown) => {
        setError(assistantErrorText(e))
        setBusy(false)
      },
    )
  }

  return (
    <Card title={REMOTE_UPLOADS_TITLE} aria-label={REMOTE_UPLOADS_TITLE} meta={uploads === null ? undefined : uploads ? 'On' : 'Off'}>
      {developerMode === null || uploads === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="setting-list">
          <Field label="Allow uploads from away" help="Lets an admin signed in on another device upload audio, video, subtitle files, novel text and comic pages. It turns itself off after 7 days, and whenever Developer Mode is off.">
            <Toggle checked={uploads} disabled={busy || (!developerMode && !uploads)} onChange={flip} />
          </Field>
          {!developerMode && (
            <p className="muted">
              Needs Developer Mode: turn it on in <a href={routeHref({ name: 'settings', section: 'developer-mode' })}>Settings &gt; Advanced</a>.
            </p>
          )}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Card>
  )
}
