import type { ReactNode } from 'react'

import { Badge } from '../../components/Badge'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import type { DiagnosticsModelCache, DiagnosticsSetupChecks, GpuStatus, ModelEngineVersion } from '../../types/diagnostics'
import { setupRows, setupSummary } from './diagnosticsAdmin'
import { ModelsList } from './ModelsList'
import { PyannoteSection } from './PyannoteSection'

/**
 * "Setup": every check (Python, ffmpeg with libass, JS runtime, GPU, app
 * files, library folder) as a row with an OK/Problem badge, problems first,
 * then speaker detection and the models. A fold: open by default only while
 * something is wrong.
 */
export function SetupSection({ checks, gpu, engines, cache, pc, checking, onRecheck, onCacheChanged, children }: {
  checks: DiagnosticsSetupChecks
  gpu: GpuStatus | null
  engines: ModelEngineVersion[]
  cache: DiagnosticsModelCache | null
  pc: PcMode
  checking: boolean
  onRecheck: () => void
  onCacheChanged: () => void
  // Fixes shown under the rows (the Deno install).
  children?: ReactNode
}) {
  const rows = setupRows(checks, gpu)
  const sorted = [...rows.filter((r) => r.problem), ...rows.filter((r) => !r.problem)]
  const summary = setupSummary(rows)
  return (
    <Section key={gpu ? 'gpu' : 'no-gpu'} title="Setup" storageKey="diagnostics.setup" summary={summary} defaultOpen={rows.some((r) => r.problem)}>
      <div className="actions">
        <span className="muted" data-testid="setup-summary">{summary}</span>
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={checking} onClick={onRecheck}>
          {checking ? 'Checking…' : 'Check again'}
        </button>
      </div>
      <ul data-testid="setup-rows" className="setup-list" aria-label="Setup checks">
        {sorted.map((r) => (
          <li key={r.key} className={r.problem ? 'problem' : undefined}>
            <span className="setup-name">{r.label}</span>
            <span className="setup-value">{r.value}</span>
            <Badge tone={r.problem ? 'warn' : 'ok'}>{r.problem ? 'Problem' : 'OK'}</Badge>
          </li>
        ))}
      </ul>
      {children}
      <PyannoteSection />
      <ModelsList engines={engines} cache={cache} pc={pc} onChanged={onCacheChanged} />
    </Section>
  )
}
