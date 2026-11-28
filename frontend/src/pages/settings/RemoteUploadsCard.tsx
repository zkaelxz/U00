/*
 * People and access > Uploads from away: the PC's switch that lets an admin upload from another device.
 * It stays on until turned off; an opt-in turns it off after 7 days (services/settings_service.remote_uploads_on). PC only.
 */
import { useEffect, useState } from 'react'

import { getPcMode, loadPcMode, pcOnlyFetch } from '../../api/pcOnly'
import { getSettings, updateSetting } from '../../api/settings'
import type { SettingsOverview } from '../../types/settings'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { usePcOnly } from '../../hooks/usePcOnly'
import { assistantErrorText } from '../assistant/assistantFormat'

export const REMOTE_UPLOADS_TITLE = 'Uploads from away'

function onSince(since: number | null, autoOff: boolean): string {
  const when = since === null ? '' : ` since ${new Date(since * 1000).toLocaleDateString()}`
  return `On${when}${autoOff ? ', turns off after 7 days' : ''}`
}

export function RemoteUploadsCard() {
  if (usePcOnly() === 'remote') return null
  return <RemoteUploadsControls />
}

function RemoteUploadsControls() {
  const [uploads, setUploads] = useState<boolean | null>(null)
  const [since, setSince] = useState<number | null>(null)
  const [autoOff, setAutoOff] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Another device, or an older server: nothing to offer.
  const [hidden, setHidden] = useState(false)

  const apply = (s: SettingsOverview) => {
    setUploads(s.remote_uploads)
    setSince(s.remote_uploads_since ?? null)
    setAutoOff(s.remote_uploads_auto_off)
  }

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() !== 'local') return setHidden(true)
      getSettings().then(
        (s) => live && apply(s),
        (e: unknown) => live && setError(assistantErrorText(e)),
      )
    })
    return () => {
      live = false
    }
  }, [])

  if (hidden) return null

  const flip = (key: 'remote_uploads' | 'remote_uploads_auto_off', next: boolean) => {
    setBusy(true)
    setError(null)
    updateSetting(key, next, pcOnlyFetch()).then(
      (s) => {
        apply(s)
        setBusy(false)
      },
      (e: unknown) => {
        setError(assistantErrorText(e))
        setBusy(false)
      },
    )
  }

  return (
    <Section title={REMOTE_UPLOADS_TITLE} summary={uploads === null ? undefined : uploads ? onSince(since, autoOff) : 'Off'}>
      {uploads === null ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="setting-list">
          <Field label="Allow uploads from away" help="Lets an admin signed in on another device upload audio, video, subtitle files, novel text and comic pages. It stays on until you turn it off.">
            <Toggle checked={uploads} disabled={busy} onChange={(v) => flip('remote_uploads', v)} />
          </Field>
          {uploads && (
            <p className="muted">{onSince(since, autoOff)}</p>
          )}
          <Field label="Turn off automatically after 7 days" help="Off by default. When on, the switch above turns itself off 7 days after it was turned on (or after you chose this).">
            <Toggle checked={autoOff} disabled={busy} onChange={(v) => flip('remote_uploads_auto_off', v)} />
          </Field>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
