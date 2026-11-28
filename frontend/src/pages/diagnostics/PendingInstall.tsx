import { useState } from 'react'

import { api } from '../../api/client'
import { restartAndInstall } from '../../api/pendingInstall'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useInterval } from '../../hooks/useInterval'

import { Badge } from '../../components/Badge'
import { buttonClass } from '../../components/uiClasses'
import type { PendingInstallPlan, PendingInstallQueued, PendingInstallStatus } from '../../types/pendingInstall'

export interface PlannedInstall {
  label: string
  keys: string[]
  plan: PendingInstallPlan
  // The installer as it ran before queuing existed: used when nothing is in use.
  // acceptedRisks: the needs_confirm lines the owner accepted (else empty).
  installNow: (acceptedRisks: string[]) => void
  // False for an update: the restart queue installs package keys, which for an installed package changes nothing.
  queueable?: boolean
  // How an update (not a plain install) is queued for the restart.
  queue?: (acceptRisk: boolean) => Promise<PendingInstallQueued>
}

const RESTART_HELP =
  'Close Baihe (the "Stop Baihe Studio" shortcut, or close its server window) and open it again. Nothing is closed for you.'

/** What would change, in plain words, and the buttons that start it. */
export function InstallPlanPanel({ planned, queueing = false, onQueue, onCancel }: {
  planned: PlannedInstall
  // The queue request is in flight: a second click would send it twice.
  queueing?: boolean
  onQueue: (acceptRisk: boolean) => void
  onCancel: () => void
}) {
  const { plan, label } = planned
  const [understood, setUnderstood] = useState(false)
  const refused = plan.blocked.length > 0
  const needsRisk = plan.needs_confirm.length > 0
  const gated = needsRisk && !understood
  const restart = plan.mode === 'restart'
  // Nothing to start now and no queue to put it in: the owner restarts first.
  const waitForRestart = restart && planned.queueable === false
  return (
    <div className="diag-stack" role="group" aria-label={`Before installing ${label}`} data-testid="install-plan">
      <h2 className="subhead">Before installing {label}</h2>
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
        <p data-testid="install-plan-restart">
          {waitForRestart ? `Restart Baihe, then try again before anything uses it. ${RESTART_HELP}` : `Installs when you restart Baihe. ${RESTART_HELP}`}
        </p>
      )}
      <div className="actions">
        {!refused && !waitForRestart && (
          <button type="button" className={buttonClass('primary', 'sm')} disabled={gated || (restart && queueing)}
            onClick={() => (restart ? onQueue(understood) : planned.installNow(understood ? plan.needs_confirm : []))}>
            {restart ? 'Install when I restart Baihe' : 'Install now'}
          </button>
        )}
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={onCancel}>
          {refused || waitForRestart ? 'Close' : 'Cancel'}
        </button>
      </div>
    </div>
  )
}

const RESTART_POLL_MS = 3000
const RESTART_GIVE_UP_MS = 15 * 60 * 1000

/** "Restart and install now": names the jobs a restart would stop and asks before it does anything. */
export function RestartAndInstall({ status, onBack }: { status: PendingInstallStatus; onBack: () => void }) {
  const { restart } = status
  const [asking, setAsking] = useState(false)
  const [stopJobs, setStopJobs] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [waiting, setWaiting] = useState<{ since: number; sawDown: boolean } | null>(null)
  const [notBack, setNotBack] = useState(false)
  const jobs = restart.running_jobs
  const blockedByJobs = restart.other_jobs || (jobs.length > 0 && !stopJobs)

  useInterval(() => {
    if (!waiting) return
    api.health().then(
      () => { if (waiting.sawDown) { setWaiting(null); onBack() } },
      () => setWaiting((w) => (w ? { ...w, sawDown: true } : w)),
    )
    if (Date.now() - waiting.since > RESTART_GIVE_UP_MS) { setWaiting(null); setNotBack(true) }
  }, waiting ? RESTART_POLL_MS : null)

  const go = async () => {
    setBusy(true)
    setError(null)
    try {
      await restartAndInstall(stopJobs)
      setWaiting({ since: Date.now(), sawDown: false })
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  if (waiting) {
    return <p role="status" data-testid="restart-install-waiting">
      Restarting Baihe and installing {status.packages.join(', ')}. This can take several minutes. This page reloads when Baihe is back.
    </p>
  }
  return (
    <div className="diag-stack" data-testid="restart-install">
      <ErrorBanner error={error} describe={{ pcOnly: true, serverText: true }} onDismiss={() => setError(null)} />
      {notBack && <p role="status">Baihe hasn't come back yet. Check the PC.</p>}
      {!restart.available && <p className="muted" data-testid="restart-install-reason">{restart.reason}</p>}
      {restart.available && !asking && (
        <div className="actions">
          <button type="button" className={buttonClass('primary', 'sm')} onClick={() => setAsking(true)}>
            Restart and install now…
          </button>
        </div>
      )}
      {restart.available && asking && (
        <div className="diag-stack" role="group" aria-label="Restart and install">
          <p>Baihe will close, install {status.packages.join(', ')}, and open again. The window reconnects by itself.</p>
          {restart.other_jobs && <p className="error" role="alert">A job is running in another Baihe process. Let it finish first.</p>}
          {jobs.length > 0 && (
            <>
              <p className="warn" role="alert">These jobs are running and a restart stops them: {jobs.join(', ')}.</p>
              <label>
                <input type="checkbox" checked={stopJobs} onChange={(e) => setStopJobs(e.target.checked)} />{' '}
                Stop these jobs and restart anyway
              </label>
            </>
          )}
          <div className="actions">
            <button type="button" className={buttonClass('primary', 'sm')} disabled={busy || blockedByJobs}
              onClick={() => void go()}>
              Confirm: restart and update
            </button>
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setAsking(false)}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}

/** What the update helper's last automatic restart did, in plain words. */
function restartLine(r: NonNullable<PendingInstallStatus['last_restart']>): string {
  const away = `${r.downtime_seconds ?? 0} seconds`
  if (r.outcome === 'ok') return `Baihe restarted itself to install ${r.packages ?? 0} packages and was back after ${away}.`
  if (r.outcome === 'install_failed') {
    return `Baihe restarted itself, but the install did not work. ${r.restored ?? 0} earlier packages were put back${r.restore_failed ? `, ${r.restore_failed} could not be` : ''}. Baihe was back after ${away}; queue it again if you still want it.`
  }
  return 'Baihe tried to restart itself to install packages but it did not finish cleanly, and the queued install was removed. Check Packages, run Setup again to repair, then queue it again.'
}

/** The queued install (with Cancel) and the last start-up outcome (with Dismiss). */
export function PendingInstallBanner({ status, onCancel, onDismiss, onRestarted }: {
  status: PendingInstallStatus
  onCancel: () => void
  onDismiss: () => void
  onRestarted: () => void
}) {
  const result = status.result
  return (
    <>
      {status.packages.length > 0 && (
        <div className="diag-stack" role="status" data-testid="pending-install">
          <p>
            <Badge tone="warn">pending install (restart)</Badge>{' '}
            {status.restart.automatic
              ? `${status.packages.join(', ')} will install by themselves: Baihe restarts once no job or Live session is running (a few minutes after you queued it).`
              : `${status.packages.join(', ')} will install when you restart Baihe.`}
            {/* The service's reason, shown below, says how it restarts: closing a window doesn't. */}
            {status.restart.service || status.restart.automatic ? '' : ` ${RESTART_HELP}`}
          </p>
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={onCancel}>
              Cancel install
            </button>
          </div>
          <RestartAndInstall status={status} onBack={onRestarted} />
        </div>
      )}
      {status.last_restart && (
        <p className={status.last_restart.outcome === 'ok' ? undefined : 'error'} role="status" data-testid="pending-install-last-restart">
          {restartLine(status.last_restart)}
        </p>
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
