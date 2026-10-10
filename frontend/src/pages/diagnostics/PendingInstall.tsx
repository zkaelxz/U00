import { useState } from 'react'

import { Badge } from '../../components/Badge'
import { buttonClass } from '../../components/uiClasses'
import type { PendingInstallPlan, PendingInstallStatus } from '../../types/pendingInstall'

export interface PlannedInstall {
  label: string
  keys: string[]
  plan: PendingInstallPlan
  // The installer as it ran before queuing existed: used when nothing is in use.
  installNow: () => void
}

// A plain preview is only worth a click when something is replaced, risky,
// refused or has to wait; a fresh install of new packages goes straight ahead.
export function needsPlanPanel(plan: PendingInstallPlan): boolean {
  return plan.blocked.length > 0 || plan.mode === 'restart' || plan.needs_confirm.length > 0
    || plan.changes.some((c) => c.kind !== 'install')
}

export const RESTART_HELP =
  'Close Baihe (the "Stop Baihe Studio" shortcut, or close its server window) and open it again. Nothing is closed for you.'

export function pendingKeys(status: PendingInstallStatus | null): Set<string> {
  return new Set(status?.packages ?? [])
}

/** What would change, in plain words, and the buttons that start it. */
export function InstallPlanPanel({ planned, onQueue, onCancel }: {
  planned: PlannedInstall
  onQueue: (acceptRisk: boolean) => void
  onCancel: () => void
}) {
  const { plan, label } = planned
  const [understood, setUnderstood] = useState(false)
  const refused = plan.blocked.length > 0
  const needsRisk = plan.needs_confirm.length > 0
  const gated = needsRisk && !understood
  const restart = plan.mode === 'restart'
  return (
    <div className="diag-stack" role="group" aria-label={`Before installing ${label}`} data-testid="install-plan">
      <h4>Before installing {label}</h4>
      {plan.summary.length > 0
        ? <ul aria-label="What will change">{plan.summary.map((l) => <li key={l}>{l}</li>)}</ul>
        : <p className="muted">No changes to other packages were found.</p>}
      {plan.note && <p className="muted" data-testid="install-plan-note">{plan.note}</p>}
      {plan.blocked.map((b) => <p key={b} className="error" role="alert">{b}</p>)}
      {needsRisk && !refused && (
        <>
          {plan.needs_confirm.map((n) => <p key={n} className="warn">{n}</p>)}
          <label>
            <input type="checkbox" checked={understood} onChange={(e) => setUnderstood(e.target.checked)} />{' '}
            I understand, install anyway
          </label>
        </>
      )}
      {restart && !refused && (
        <p data-testid="install-plan-restart">Installs when you restart Baihe. {RESTART_HELP}</p>
      )}
      <div className="actions">
        {!refused && (
          <button type="button" className={buttonClass('primary', 'sm')} disabled={gated}
            onClick={() => (restart ? onQueue(understood) : planned.installNow())}>
            {restart ? 'Install when I restart Baihe' : 'Install now'}
          </button>
        )}
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={onCancel}>
          {refused ? 'Close' : 'Cancel'}
        </button>
      </div>
    </div>
  )
}

/** The queued install (with Cancel) and the last start-up outcome (with Dismiss). */
export function PendingInstallBanner({ status, onCancel, onDismiss }: {
  status: PendingInstallStatus
  onCancel: () => void
  onDismiss: () => void
}) {
  const result = status.result
  return (
    <>
      {status.packages.length > 0 && (
        <div className="diag-stack" role="status" data-testid="pending-install">
          <p>
            <Badge tone="warn">pending install (restart)</Badge>{' '}
            {status.packages.join(', ')} will install when you restart Baihe. {RESTART_HELP}
          </p>
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={onCancel}>
              Cancel the queued install
            </button>
          </div>
        </div>
      )}
      {status.problem && <p className="error" role="alert" data-testid="pending-install-problem">{status.problem}</p>}
      {result && (
        <div className="diag-stack" data-testid="pending-install-result" role={result.status === 'ok' ? 'status' : 'alert'}>
          <p className={result.status === 'ok' || result.status === 'running' ? undefined : 'error'}>{result.message}</p>
          {result.tail.length > 0 && result.status !== 'ok' && <pre className="diag-pre">{result.tail.join('\n')}</pre>}
          {result.status !== 'running' && (
            <div className="actions">
              <button type="button" className={buttonClass('secondary', 'sm')} onClick={onDismiss}>Dismiss</button>
            </div>
          )}
        </div>
      )}
    </>
  )
}
