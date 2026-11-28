/*
 * Settings > My account and look > Phone app: the Android app's own switches (alerts and the copied-link offer). Shown only
 * inside the app; they are saved on the phone, not on the PC.
 */
import { useEffect, useState } from 'react'

import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import {
  canChangeAddress, canCheckForUpdates, changeShellAddress, checkForUpdates, setShellSwitch, shellNotificationsBlocked, shellSwitch, type ShellSwitch,
} from '../../phoneShell'

export const ALERTS_BLOCKED =
  "Android is blocking notifications for Baihe, so no alerts arrive. Turn them on in the phone's Settings > Apps > Baihe Studio > Notifications."

function ShellToggle({ name, label, help, error }: { name: ShellSwitch; label: string; help: string; error?: string | null }) {
  const [on, setOn] = useState(() => shellSwitch(name))
  return (
    <Field label={label} help={help} error={on ? error : null}>
      <Toggle checked={on} onChange={(next) => { setShellSwitch(name, next); setOn(next) }} />
    </Field>
  )
}

// Polled, because Android's permission prompt and its own settings screen give the page no event when they close.
function useNotificationsBlocked(): boolean {
  const [blocked, setBlocked] = useState(shellNotificationsBlocked)
  useEffect(() => {
    const timer = window.setInterval(() => setBlocked(shellNotificationsBlocked()), 2000)
    return () => window.clearInterval(timer)
  }, [])
  return blocked
}

export function PhoneAppCard() {
  const blocked = useNotificationsBlocked()
  return (
    <Card title="Phone app" meta="This phone only" aria-label="Phone app">
      <div className="setting-list">
        <ShellToggle
          name="alerts_on"
          label="Alerts on this phone"
          help="Shows finished titles, failed jobs, new chapters and live channels as phone notifications when the app is closed, also after the phone restarts. A tap opens what the alert is about; several at once open Baihe. Checks run about every 15 minutes, and every 3 minutes for two hours after you leave the app. Needs you to be signed in on this phone's Studio tab."
          error={blocked ? ALERTS_BLOCKED : null}
        />
        <ShellToggle
          name="clipboard_offer_on"
          label="Offer to subtitle or read a copied link"
          help="When you open Baihe with a video or comic link just copied, asks once per link whether to subtitle it or Quick read it. Android may show its own 'pasted from your clipboard' note when Baihe looks."
        />
        <ShellToggle
          name="pip_on"
          label="Float Live in a small window when I leave the app"
          help="While a Live picture is playing, going Home keeps it in a small window over other apps, with the captions on it. Turn off Captions over video in Live and the window shows only the picture."
        />
        {canChangeAddress() && (
          <Field label="Baihe address" help="The address this app opens. Change it when your PC has a new address; you sign in again afterwards. Grab keeps its own server address in Grab > Settings.">
            <button type="button" className="btn" onClick={changeShellAddress}>Change address</button>
          </Field>
        )}
        {canCheckForUpdates() && (
          <Field label="App updates" help="Looks for a newer build of this app right now and offers to install it. The app also checks by itself whenever you open it.">
            <button type="button" className="btn" onClick={checkForUpdates}>Check for updates</button>
          </Field>
        )}
      </div>
    </Card>
  )
}
