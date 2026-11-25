import { useCallback, useState } from 'react'

import { listUserDevices, revokeUserDevice } from '../../api/adminUsers'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import type { AdminDevice } from '../../types/adminUsers'
import { deviceDetails, deviceName } from '../settings/deviceSessionsModel'

/** One person's signed-in devices, loaded when asked for, each with its own Sign out. `block` is why
 *  signing out is not possible here (your own account, an admin's device away from the PC). */
export function MemberDevices({ userId, email, block }: { userId: number; email: string; block: string | null }) {
  const [devices, setDevices] = useState<AdminDevice[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<number | null>(null)

  const load = useCallback(() => {
    listUserDevices(userId).then((r) => setDevices(r.devices), setError)
  }, [userId])

  const signOut = async (d: AdminDevice) => {
    setBusy(d.id)
    setError(null)
    try {
      await revokeUserDevice(userId, d.id)
    } catch (e) {
      setError(e)
    }
    setBusy(null)
    load()
  }

  if (devices === null) {
    return (
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={load}>Show devices</button>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </div>
    )
  }
  const now = Date.now()
  return (
    <div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {devices.length === 0 ? <p className="muted">No device is signed in.</p> : (
        <ul className="admin-user-devices">
          {devices.map((d) => (
            <li key={d.id}>
              <strong>{d.device}</strong>
              <div className="muted">{deviceDetails({ ...d, current: false }, now)}</div>
              <ConfirmButton name={deviceName({ ...d, current: false })} label="Sign out…" verb={`sign ${email} out of`} tone="primary"
                busy={busy === d.id} disabled={busy !== null || !!block} title={block ?? undefined} onConfirm={() => void signOut(d)} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
