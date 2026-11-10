/*
 * Settings > PC profiles: a PIN that makes the PC window open on Guest, which
 * leaves private titles out of every list until the Owner enters the PIN.
 * It hides titles from view, it does not lock the files or the data.
 */
import { useEffect, useState } from 'react'

import {
  clearPcProfilePin, getPcProfile, PIN_HINT, setPcProfilePin, validPin, type PcProfileStatus,
} from '../../api/pcProfile'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'

export const PC_PROFILE_TITLE = 'PC profiles'

export function PcProfileCard() {
  const [status, setStatus] = useState<PcProfileStatus | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [touched, setTouched] = useState(false)
  const [hideAll, setHideAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')

  useEffect(() => {
    getPcProfile().then(setStatus, () => setStatus(null))
  }, [])
  // Not the PC, or an older server: nothing to offer.
  if (!status) return null

  const run = async (work: () => Promise<PcProfileStatus & { hidden?: number }>, done: (r: { hidden?: number }) => string) => {
    setBusy(true)
    setNote('')
    setError(null)
    try {
      const result = await work()
      setStatus(result)
      setCurrent('')
      setNext('')
      setTouched(false)
      setNote(done(result))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const save = () => {
    setTouched(true)
    if (!validPin(next) || (status.pin_set && !validPin(current))) return
    const first = !status.pin_set
    void run(
      () => setPcProfilePin(next, status.pin_set ? current : undefined, first && hideAll),
      (r) => `Saved. This PC now opens on Guest.${r.hidden ? ` ${r.hidden} titles or series are now private.` : ''}`,
    )
  }

  // Guest cannot change the PIN: the server refuses it, so the form says why instead.
  const guest = status.guest
  return (
    <Card title={PC_PROFILE_TITLE} aria-label={PC_PROFILE_TITLE}>
      <div className="settings-group" data-testid="pc-profile">
        <p className="settings-note">
          Set a PIN and this PC opens on Guest, which leaves out every title you marked private.
          Choose Owner and enter the PIN to see them again. This hides titles from view; it does not lock the files, and Guest keeps the owner's other powers. Only for this PC window with sign-in off.
        </p>
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {guest ? (
          <p className="muted">Switch to Owner (top right) to change the PIN.</p>
        ) : (
          <form className="diag-stack" onSubmit={(e) => { e.preventDefault(); save() }}>
            {status.pin_set && (
              <Field label="Current PIN" error={touched && !validPin(current) ? PIN_HINT : null}>
                <input type="password" inputMode="numeric" autoComplete="off" maxLength={12} value={current}
                  onChange={(e) => setCurrent(e.target.value)} />
              </Field>
            )}
            <Field label={status.pin_set ? 'New PIN' : 'PIN'} help={PIN_HINT}
              error={touched && !validPin(next) ? PIN_HINT : null}>
              <input type="password" inputMode="numeric" autoComplete="off" maxLength={12} value={next}
                onChange={(e) => setNext(e.target.value)} />
            </Field>
            {!status.pin_set && (
              <label className="checkbox-row">
                <input type="checkbox" checked={hideAll} onChange={(e) => setHideAll(e.target.checked)} />
                Also mark all my current titles private (household members stop seeing them too)
              </label>
            )}
            <div className="settings-actions">
              <button type="submit" className={buttonClass('primary')} disabled={busy} aria-busy={busy}>
                {status.pin_set ? 'Change PIN' : 'Turn on profiles'}
              </button>
              {status.pin_set && (
                <button type="button" className={buttonClass()} disabled={busy || !validPin(current)}
                  onClick={() => void run(() => clearPcProfilePin(current), () => 'Profiles are off.')}>
                  Turn off (enter current PIN)
                </button>
              )}
            </div>
          </form>
        )}
        <span className="muted" aria-live="polite">{note}</span>
      </div>
    </Card>
  )
}
