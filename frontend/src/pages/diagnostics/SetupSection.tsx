import { Section } from '../../components/Section'
import type { DiagnosticsSetupChecks, GpuStatus, ModelEngineVersion } from '../../types/diagnostics'
import { engineRow, setupRows, setupSummary } from './diagnosticsAdmin'
import './diagnosticsParity.css'

/**
 * The core checks (Python, ffmpeg with libass, JS runtime for yt-dlp) always
 * shown at the top, then "Setup": GPU, app files, library folder, model engines.
 */
export function SetupSection({ checks, gpu, engines, checking, onRecheck }: {
  checks: DiagnosticsSetupChecks
  gpu: GpuStatus | null
  engines: ModelEngineVersion[]
  checking: boolean
  onRecheck: () => void
}) {
  const rows = setupRows(checks, gpu)
  const core = rows.filter((r) => r.core)
  const rest = rows.filter((r) => !r.core)
  const problems = rest.some((r) => r.problem)
  return (
    <>
      <ul data-testid="system-summary" className="diag-rows diag-core" aria-label="Core checks">
        {core.map((r) => (
          <li key={r.key} className={r.problem ? 'warn' : undefined}>
            {r.text}
          </li>
        ))}
      </ul>
      <Section title="Setup" storageKey="diagnostics.setup" defaultOpen={problems} summary={setupSummary(rest)}>
        <ul data-testid="setup-rows" className="diag-rows">
          {rest.map((r) => (
            <li key={r.key} className={r.problem ? 'warn' : undefined}>
              {r.text}
            </li>
          ))}
        </ul>
        {engines.length > 0 && (
          <Section title="Model engines" count={engines.length} storageKey="diagnostics.setupEngines">
            <ul className="diag-rows" aria-label="Model engines">
              {engines.map((m) => (
                <li key={m.name}>{engineRow(m)}</li>
              ))}
            </ul>
          </Section>
        )}
        <div className="actions">
          <button type="button" disabled={checking} onClick={onRecheck}>
            {checking ? 'Checking…' : 'Check again'}
          </button>
        </div>
      </Section>
    </>
  )
}
