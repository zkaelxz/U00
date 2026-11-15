/*
 * People and access > Set up remote access: the steps of docs/household-access.md in
 * order, each with what the app can see about it. Read-only and PC only; the API
 * answers states and fixed sentences, never the public name, the Google id or secret.
 * Steps the PC can't see (the router) say so instead of guessing.
 */
import { useCallback, useEffect, useState } from 'react'

import { getRemoteSetup } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import type { RemoteSetup } from '../../types/diagnostics'
import { setupSummary, stepBadge } from './remoteSetupModel'

export function RemoteSetupSection({ pc }: { pc: PcMode }) {
  if (pc === 'remote') return null
  return <RemoteSetupBody />
}

function RemoteSetupBody() {
  const [setup, setSetup] = useState<RemoteSetup | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setBusy(true)
    getRemoteSetup().then(
      (r) => {
        setSetup(r)
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }, [])

  useEffect(load, [load])

  return (
    <Section title="Set up remote access" storageKey="admin.remote-setup" summary={setup ? setupSummary(setup) : undefined}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
      {!setup ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <ol className="diag-rows diag-stack" data-testid="remote-setup-steps">
            {setup.steps.map((s) => {
              const badge = stepBadge(s)
              return (
                <li key={s.id} data-testid={`remote-setup-${s.id}`}>
                  <strong>{s.title}</strong> <Badge tone={badge.tone}>{badge.label}</Badge>
                  {s.id === setup.next && <Badge tone="neutral">Next</Badge>}
                  <br />
                  <span className="muted">{s.message}</span>
                </li>
              )
            })}
          </ol>
          <div className="settings-actions">
            <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={load}>
              {busy ? 'Checking…' : 'Check again'}
            </button>
          </div>
        </>
      )}
    </Section>
  )
}
