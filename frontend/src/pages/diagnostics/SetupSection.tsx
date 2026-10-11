import { Badge } from '../../components/Badge'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { capFirst } from '../../labels'
import type { DiagnosticsSetupChecks, GpuStatus } from '../../types/diagnostics'
import { setupRows, setupSummary } from './diagnosticsAdmin'

/**
 * "Setup": every check (Python, ffmpeg with libass, JS runtime, GPU, app
 * files, library folder) as a row with an OK/Problem badge, problems first.
 * The fixes (browser support, Deno) are cards on the Packages and installs tab.
 */
export function SetupSection({ checks, gpu, openSignal, checking, onRecheck }: {
  checks: DiagnosticsSetupChecks
  gpu: GpuStatus | null
  openSignal: number
  checking: boolean
  onRecheck: () => void
}) {
  const rows = setupRows(checks, gpu)
  const sorted = [...rows.filter((r) => r.problem), ...rows.filter((r) => !r.problem)]
  return (
    <Section key={gpu ? 'gpu' : 'no-gpu'} title="Setup" storageKey="diagnostics.setup" summary={setupSummary(rows)} defaultOpen openSignal={openSignal}>
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={checking} onClick={onRecheck}>
          {checking ? 'Checking…' : 'Check again'}
        </button>
      </div>
      <ul data-testid="setup-rows" className="setup-list" aria-label="Setup checks">
        {sorted.map((r) => (
          <li key={r.key} className={r.problem ? 'problem' : undefined}>
            <span className="setup-name">{r.label}</span>
            <span className="setup-value">{capFirst(r.value)}</span>
            <Badge tone={r.problem ? 'warn' : 'ok'}>{r.problem ? 'Problem' : 'OK'}</Badge>
          </li>
        ))}
      </ul>
      {checks.warnings?.map((w) => <p key={w} className="warn" data-testid="setup-warning">{w}</p>)}
    </Section>
  )
}
