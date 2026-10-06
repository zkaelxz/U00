import type { ReactNode } from 'react'

import { Badge } from '../../components/Badge'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import { capFirst } from '../../labels'
import type { DiagnosticsModelCache, DiagnosticsSetupChecks, GpuStatus, ModelEngineVersion } from '../../types/diagnostics'
import { setupRows, setupSummary } from './diagnosticsAdmin'
import type { AdminBusy } from './diagnosticsAdmin'
import { ModelsList } from './ModelsList'
import { PyannoteSection } from './PyannoteSection'

/**
 * "Setup": every check (Python, ffmpeg with libass, JS runtime, GPU, app
 * files, library folder) as a row with an OK/Problem badge, problems first,
 * then speaker detection and the models. A fold: open by default only while
 * something is wrong.
 */
export function SetupSection({ checks, gpu, engines, installable, cache, pc, jobsActive, busy, onBusy, openSignal, checking, onRecheck, onCacheChanged, onInstalled, children }: {
  checks: DiagnosticsSetupChecks
  gpu: GpuStatus | null
  engines: ModelEngineVersion[]
  // Names of the engines that can be installed from here.
  installable: Set<string>
  cache: DiagnosticsModelCache | null
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onBusy: (b: AdminBusy) => void
  // Changes each time something elsewhere asks for the fold to open.
  openSignal: number
  checking: boolean
  onRecheck: () => void
  onCacheChanged: () => void
  onInstalled: () => void
  // Fixes shown under the rows (the Deno install).
  children?: ReactNode
}) {
  const rows = setupRows(checks, gpu)
  const sorted = [...rows.filter((r) => r.problem), ...rows.filter((r) => !r.problem)]
  const summary = setupSummary(rows)
  return (
    <Section key={gpu ? 'gpu' : 'no-gpu'} title="Setup" storageKey="diagnostics.setup" summary={summary} defaultOpen={rows.some((r) => r.problem)} openSignal={openSignal}>
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
            <span className="setup-value">{capFirst(r.value)}</span>
            <Badge tone={r.problem ? 'warn' : 'ok'}>{r.problem ? 'Problem' : 'OK'}</Badge>
          </li>
        ))}
      </ul>
      {checks.warnings?.map((w) => <p key={w} className="warn" data-testid="setup-warning">{w}</p>)}
      {children}
      <PyannoteSection />
      <ModelsList engines={engines} installable={installable} cache={cache} pc={pc} jobsActive={jobsActive}
        busy={busy} onBusy={onBusy} onChanged={onCacheChanged} onInstalled={onInstalled} />
    </Section>
  )
}
