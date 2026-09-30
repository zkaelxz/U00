/*
 * Settings > Signed-in devices: every device where this person is signed in
 * (a coarse "Chrome on Android" label, when it was used, its network), this
 * one first. Any other device can be signed out on its own, or all of them
 * at once, each after a confirm step; a lost phone is signed out at once
 * (its next request, and its live updates, stop). Signing out all other
 * devices also gives this one a new sign-in (the server sets new cookies).
 * This device signs out with the usual Sign out. An admin can sign devices
 * out only on the main PC (the server answers 403 elsewhere), so away from
 * it the buttons are off with the reason. Shown only to a signed-in person:
 * the owner at the PC with sign-in off has no sessions.
 */
import { useCallback, useEffect, useState } from 'react'

import { listDeviceSessions, signOutDevice, signOutOtherDevices } from '../../api/deviceSessions'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { usePcOnly } from '../../hooks/usePcOnly'
import { useSession, type SessionState } from '../../hooks/useSession'
import type { DeviceSession, DeviceSessionList } from '../../types/deviceSessions'
import {
  adminDevicesBlock,
  deviceDetails,
  deviceName,
  showDevices,
  signOutOthersBlock,
  signedOutNote,
  timeoutText,
} from './deviceSessionsModel'

const TITLE = 'Signed-in devices'

type Busy = number | 'others' | null

export function DevicesCard() {
  const session = useSession()
  if (!showDevices(session)) return null
  return <DevicesBody session={session} />
}

function DevicesBody({ session }: { session: SessionState }) {
  const adminBlock = adminDevicesBlock(session, usePcOnly())
  const [data, setData] = useState<DeviceSessionList | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<Busy>(null)
  const [note, setNote] = useState('')
  // "Last used 5 minutes ago" is as of the latest load.
  const [nowMs, setNowMs] = useState(() => Date.now())

  // Leaves the error alone: the reload after a refused action must not hide why.
  const load = useCallback(() => {
    listDeviceSessions().then((r) => {
      setNowMs(Date.now())
      setData(r)
    }, setError)
  }, [])

  useEffect(load, [load])

  const run = async (what: Exclude<Busy, null>, device?: DeviceSession) => {
    setBusy(what)
    setNote('')
    setError(null)
    try {
      if (what === 'others') {
        setNote(signedOutNote((await signOutOtherDevices()).revoked))
      } else {
        await signOutDevice(what)
        setNote(`Signed out ${device?.device ?? 'that device'}.`)
      }
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
      load()
    }
  }

  const list = data?.sessions ?? []
  const othersBlock = signOutOthersBlock(list, adminBlock)
  return (
    <Card title={TITLE} aria-label={TITLE} className="devices-card"
      meta={data ? timeoutText(data.idle_timeout_days, data.absolute_timeout_days) : undefined}>
      <div className="settings-group" data-testid="signed-in-devices">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {data === null ? (
          !error && <p className="muted">Loading…</p>
        ) : (
          <ul className="status-list" aria-label={TITLE}>
            {list.map((d) => (
              <li key={d.id}>
                <div className="status-row">
                  <span className="status-row-name">{d.device}</span>
                  {d.current && <Badge tone="info">This device</Badge>}
                </div>
                <p className="settings-note">{deviceDetails(d, nowMs)}</p>
                {!d.current && (
                  <div className="settings-actions">
                    <ConfirmButton name={deviceName(d)} label="Sign out…" verb="sign out"
                      busy={busy === d.id} disabled={busy !== null || !!adminBlock}
                      describedBy={adminBlock ? 'devices-others-why' : undefined}
                      onConfirm={() => void run(d.id, d)} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
        {data && (
          <div className="settings-actions">
            <ConfirmButton name="all other devices" label="Sign out all other devices…" verb="sign out"
              ariaLabel="Sign out all other devices" busy={busy === 'others'}
              disabled={busy !== null || !!othersBlock}
              describedBy={othersBlock ? 'devices-others-why' : undefined}
              onConfirm={() => void run('others')} />
            {othersBlock && <span className="settings-note" id="devices-others-why">{othersBlock}</span>}
          </div>
        )}
        <p className="settings-note">
          Lost a phone? Sign it out here: it can't do anything more here until someone signs in on it with Google again.
          It may still be signed in to your Google account, so also remove it there (Google Account, Security, Your devices).
          Signing out all other devices also renews this device's sign-in, so a copy of it stops working too.
          To sign out this device, use Sign out in the account menu.
        </p>
        <span className="muted" aria-live="polite">{note}</span>
      </div>
    </Card>
  )
}
