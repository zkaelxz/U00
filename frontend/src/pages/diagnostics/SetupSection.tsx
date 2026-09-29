import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { DiagnosticsSetupChecks, GpuStatus, ModelEngineVersion } from '../../types/diagnostics'
import { engineRow, setupRows, setupSummary } from './diagnosticsAdmin'

/**
 * "Setup": every check (Python, ffmpeg with libass, JS runtime, GPU, app
 * files, library folder) as a row with an OK/Problem badge, always open.
 * Problems sort first; Model engines stays a fold.
 */
export function SetupSection({ checks, gpu, engines, checking, onRecheck }: {
  checks: DiagnosticsSetupChecks
  gpu: GpuStatus | null
  engines: ModelEngineVersion[]
  checking: boolean
  onRecheck: () => void
}) {
  const rows = setupRows(checks, gpu)
  const sorted = [...rows.filter((r) => r.problem), ...rows.filter((r) => !r.problem)]
  return (
    <Card
      title="Setup"
      meta={<span data-testid="setup-summary">{setupSummary(rows)}</span>}
      aria-label="Setup"
      actions={
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={checking} onClick={onRecheck}>
          {checking ? 'Checking…' : 'Check again'}
        </button>
      }
    >
      <ul data-testid="setup-rows" className="setup-list" aria-label="Setup checks">
        {sorted.map((r) => (
          <li key={r.key} className={r.problem ? 'problem' : undefined}>
            <span className="setup-name">{r.label}</span>
            <span className="setup-value">{r.value}</span>
            <Badge tone={r.problem ? 'warn' : 'ok'}>{r.problem ? 'Problem' : 'OK'}</Badge>
          </li>
        ))}
      </ul>
      {engines.length > 0 && (
        <Section title="Model engines" count={engines.length} storageKey="diagnostics.setupEngines"
          summary={`${engines.filter((m) => m.installed).length} installed`}>
          <ul className="diag-rows" aria-label="Model engines">
            {engines.map((m) => (
              <li key={m.name}>{engineRow(m)}</li>
            ))}
          </ul>
        </Section>
      )}
    </Card>
  )
}
