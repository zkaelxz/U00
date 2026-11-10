/*
 * PcProfilePicker: "Who's using Baihe?" on the PC window when a profile PIN
 * is set. Guest opens the app without private titles; Owner asks for the PIN.
 * The choice is remembered per tab, so a new tab asks again.
 */
import { useState } from 'react'

import { lockPcProfile, rememberProfilePicked, unlockPcProfile, validPin } from '../api/pcProfile'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { buttonClass } from '../components/uiClasses'
import './login.css'

export default function PcProfilePicker({ onDone }: { onDone: () => void }) {
  const [asking, setAsking] = useState(false)
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const finish = () => {
    rememberProfilePicked(true)
    onDone()
  }

  const chooseGuest = async () => {
    setBusy(true)
    setError(null)
    try {
      await lockPcProfile()
      finish()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const chooseOwner = async () => {
    if (!validPin(pin)) return
    setBusy(true)
    setError(null)
    try {
      await unlockPcProfile(pin)
      finish()
    } catch (e) {
      setError(e)
      setPin('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="login-page" data-testid="pc-profile-picker">
      <div className="login-card">
        <h1>Who's using Baihe?</h1>
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pinText: true }} />
        {asking ? (
          <form className="diag-stack" onSubmit={(e) => { e.preventDefault(); void chooseOwner() }}>
            <Field label="PIN">
              <input type="password" inputMode="numeric" autoComplete="off" autoFocus value={pin} maxLength={12}
                onChange={(e) => setPin(e.target.value)} />
            </Field>
            <button type="submit" className={buttonClass('primary')} disabled={busy || !validPin(pin)}>
              Open as Owner
            </button>
            <button type="button" className={buttonClass()} disabled={busy} onClick={() => { setAsking(false); setPin('') }}>
              Back
            </button>
          </form>
        ) : (
          <div className="diag-stack">
            <button type="button" className={buttonClass('primary')} disabled={busy} onClick={() => setAsking(true)}>
              Owner
            </button>
            <button type="button" className={buttonClass()} disabled={busy} onClick={() => void chooseGuest()}>
              Guest
            </button>
          </div>
        )}
      </div>
    </main>
  )
}
