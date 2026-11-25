/*
 * Settings > My account and look > Phone app: the Android app's own switches (alerts and the copied-link offer). Shown only
 * inside the app; they are saved on the phone, not on the PC.
 */
import { useState } from 'react'

import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { setShellSwitch, shellSwitch, type ShellSwitch } from '../../phoneShell'

function ShellToggle({ name, label, help }: { name: ShellSwitch; label: string; help: string }) {
  const [on, setOn] = useState(() => shellSwitch(name))
  return (
    <Field label={label} help={help}>
      <Toggle checked={on} onChange={(next) => { setShellSwitch(name, next); setOn(next) }} />
    </Field>
  )
}

export function PhoneAppCard() {
  return (
    <Card title="Phone app" meta="This phone only" aria-label="Phone app">
      <div className="setting-list">
        <ShellToggle
          name="alerts_on"
          label="Alerts on this phone"
          help="Shows finished titles, failed jobs and live channels as phone notifications when the app is closed. A tap opens the page. Checks run about every 15 minutes, and every 3 minutes for two hours after you leave the app."
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
      </div>
    </Card>
  )
}
