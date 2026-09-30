import { Section } from '../../components/Section'
import type { DiagnosticsUpgradeCheckState } from '../../types/diagnosticsInstalls'
import { jobProgressLine, jobRunning } from './jobPoll'
import { testDetails, testIsFor, testLine } from './upgradeTestText'

/**
 * Under an installed package's row: the "Test first" run for its current
 * update target (progress while it runs, then the verdict and details).
 * Renders nothing when the last test was for another package or version.
 */
export function UpgradeTestResult({ state, name, target, error }: {
  state: DiagnosticsUpgradeCheckState | null
  name: string
  target: string
  // A refused start for this package (409/403 text), if any.
  error?: string | null
}) {
  const line = testLine(state, name, target)
  if (!line && !error) return null
  const mine = state && testIsFor(state, name, target) ? state : null
  const details = mine && !jobRunning(mine.job) ? testDetails(mine) : []
  return (
    <div className="upgrade-test diag-stack" data-testid={`upgrade-test-${name}`}>
      {error && <p className="error" role="alert">{error}</p>}
      {line && (
        <p className={line.tone === 'muted' ? 'muted' : line.tone} role={line.tone === 'error' ? 'alert' : 'status'}>
          {line.text}
        </p>
      )}
      {mine && jobRunning(mine.job) && mine.job && (
        <>
          <progress max={1} value={mine.job.progress || 0} aria-label={`Test of ${name} progress`} />
          <p className="muted" aria-live="polite">{jobProgressLine(mine.job)}</p>
        </>
      )}
      {details.length > 0 && (
        <Section key={`${name}-${target}-${mine?.result?.verdict}`} title="Test details" defaultOpen={mine?.result?.verdict !== 'safe'}>
          {details.map((d) => (
            <div key={d.title} className="diag-stack">
              <h5>{d.title}</h5>
              <pre className="diag-pre">{d.lines.join('\n')}</pre>
            </div>
          ))}
        </Section>
      )}
    </div>
  )
}
