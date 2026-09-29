import { Section } from '../../components/Section'
import type { DiagnosticsSetupChecks, GpuStatus, ModelEngineVersion } from '../../types/diagnostics'
import { engineRow, setupRows, setupSummary } from './diagnosticsAdmin'

/** "Setup": Python, ffmpeg, JS runtime, GPU, app files, library folder, model engines. */
export function SetupSection({ checks, gpu, engines, checking, onRecheck }: {
  checks: DiagnosticsSetupChecks
  gpu: GpuStatus | null
  engines: ModelEngineVersion[]
  checking: boolean
  onRecheck: () => void
}) {
  const rows = setupRows(checks, gpu)
  const problems = rows.some((r) => r.problem)
  return (
    <Section title="Setup" storageKey="diagnostics.setup" defaultOpen={problems} summary={setupSummary(rows)}>
      <ul data-testid="system-summary" className="diag-rows">
        {rows.map((r) => (
          <li key={r.key} className={r.problem ? 'warn' : undefined}>
            {r.text}
          </li>
        ))}
        {engines.map((m) => (
          <li key={m.name}>{engineRow(m)}</li>
        ))}
      </ul>
      <div className="actions">
        <button type="button" disabled={checking} onClick={onRecheck}>
          {checking ? 'Checking…' : 'Check again'}
        </button>
      </div>
    </Section>
  )
}
