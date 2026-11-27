/*
 * Remote-access health (GET /api/diagnostics/remote-health, admin.diagnostics).
 *
 * RemoteHealthBanner: in the app shell, only on the main PC (usePcOnly()
 * 'local'; never on the household listener, which also refuses the route),
 * only while the last check says warn or critical. Dismissing hides it until
 * the state changes (remoteHealthModel.ts stateKey), not for good.
 * RemoteHealthLine: the neutral "OK / Off" line on Diagnostics.
 *
 * Both read the saved result (the server checks on its own schedule) on mount
 * and every POLL_MS; a refusal (401/403) or a server without the route (404)
 * hides them and stops reading. Other errors keep the last result.
 */
import { useEffect, useState } from 'react'

import { ApiError } from '../api/client'
import { getRemoteHealth } from '../api/diagnostics'
import { usePcOnly } from '../hooks/usePcOnly'
import { routeHref } from '../router'
import type { RemoteHealth } from '../types/diagnostics'
import { Badge } from './Badge'
import {
  POLL_MS, bannerTitle, checkedLine, diagnosticsText, readDismissed, remoteHealthLabel, remoteHealthTone,
  showBanner, stateKey, writeDismissed,
} from './remoteHealthModel'
import { WarnBar } from './WarnBar'

const HIDE_ON = [401, 403, 404]

function useRemoteHealth(enabled: boolean): RemoteHealth | null {
  const [health, setHealth] = useState<RemoteHealth | null>(null)
  const [stopped, setStopped] = useState(false)
  useEffect(() => {
    if (!enabled || stopped) return
    let live = true
    const load = () =>
      getRemoteHealth().then(
        (h) => {
          if (live) setHealth(h)
        },
        (e: unknown) => {
          if (live && e instanceof ApiError && HIDE_ON.includes(e.status)) {
            setHealth(null)
            setStopped(true)
          }
        },
      )
    void load()
    const t = setInterval(() => void load(), POLL_MS)
    return () => {
      live = false
      clearInterval(t)
    }
  }, [enabled, stopped])
  return enabled && !stopped ? health : null
}

export function RemoteHealthBanner() {
  const pc = usePcOnly()
  const health = useRemoteHealth(pc === 'local')
  const [dismissed, setDismissed] = useState(readDismissed)
  if (!health || !showBanner(health, dismissed)) return null
  const dismiss = () => {
    const key = stateKey(health)
    writeDismissed(key)
    setDismissed(key)
  }
  const text = `${bannerTitle(health)}: ${health.message} ${checkedLine(health)}`
  return (
    <WarnBar
      shell
      tone={health.state === 'critical' ? 'bad' : 'warn'}
      role={health.state === 'critical' ? 'alert' : 'status'}
      testId="remote-health-banner"
      title={text}
      action={<a href={routeHref({ name: 'diagnostics' })}>Diagnostics</a>}
      onDismiss={dismiss}
    >
      {text}
    </WarnBar>
  )
}

export function RemoteHealthLine() {
  const health = useRemoteHealth(true)
  if (!health) return null
  return (
    <p className="page-meta" data-testid="remote-health-line">
      Remote access: <Badge tone={remoteHealthTone(health.state)}>{remoteHealthLabel(health.state)}</Badge>{' '}
      {diagnosticsText(health)}
      {health.state !== 'off' && <span className="muted"> {checkedLine(health)}</span>}
    </p>
  )
}
