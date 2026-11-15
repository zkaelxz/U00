/*
 * Settings > Updates: one card for everything that updates. Baihe itself (the installed app's
 * "App updates" or a start.bat copy's "Update Baihe", whichever this copy is), the phone app,
 * Python packages (a count and a link to Diagnostics > Packages, where they are installed) and
 * the browser extension. The PC-only parts are left out away from the PC.
 */
import { useEffect, useState } from 'react'

import { getInstallPresets } from '../../api/diagnostics'
import { fetchPhoneApp } from '../../api/update'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import { AppUpdatesCard } from './AppUpdatesCard'
import { SourceUpdateCard } from './SourceUpdateCard'
import { EXTENSION_UPDATE_NOTE, PHONE_APP_NOTE, UPDATES_TITLE, packagesLine, phoneAppLine } from './updateModel'

const SERVER = { pcOnly: true, serverText: true } as const

export function UpdatesCard() {
  const pc = usePcOnly()
  return (
    <Card title={UPDATES_TITLE} aria-label={UPDATES_TITLE}>
      {pc !== 'remote' && <AppUpdatesCard />}
      <SourceUpdateCard />
      {pc === 'local' && <PhoneAppBlock />}
      {pc === 'local' && <PackagesBlock />}
      <Card title="Browser extension" aria-label="Browser extension updates">
        <p className="muted">
          {EXTENSION_UPDATE_NOTE}
          {/* The Browser extension card is PC only, so away from the PC the link would lead nowhere. */}
          {pc === 'local' && <>{' '}<a href={routeHref({ name: 'settings', card: 'extension' })}>Open Browser extension</a></>}
        </p>
      </Card>
    </Card>
  )
}

function PhoneAppBlock() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [line, setLine] = useState<string | null>(null)

  async function fetchIt() {
    setBusy(true)
    setError(null)
    setLine(null)
    try {
      setLine(phoneAppLine(await fetchPhoneApp()))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Phone app" aria-label="Phone app">
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
      <div className="settings-actions">
        <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => void fetchIt()}>
          Get the newest phone app
        </button>
      </div>
      {line && <p role="status" data-testid="update-phone-line">{line}</p>}
      <p className="muted">{PHONE_APP_NOTE}</p>
    </Card>
  )
}

function PackagesBlock() {
  // The count is an extra: without it (no admin.diagnostics, or the read failed) the link still stands.
  const [outdated, setOutdated] = useState<number | null>(null)
  useEffect(() => {
    getInstallPresets().then((p) => setOutdated(p.outdated.length), () => undefined)
  }, [])
  return (
    <Card title="Packages" aria-label="Packages">
      <p data-testid="update-packages-line">
        {packagesLine(outdated)}{' '}
        <a href={routeHref({ name: 'diagnostics', card: 'packages' })}>Open Diagnostics &gt; Packages</a>
      </p>
    </Card>
  )
}
