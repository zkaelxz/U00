import { useEffect, useId, useRef, useState } from 'react'

import { installDependency, upgradeDependency } from '../../api/diagnostics'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import type { PcMode } from '../../hooks/usePcOnly'
import type { DiagnosticsOverview } from '../../types/diagnostics'
import { splitDependencies } from '../diagnosticsFormat'
import {
  LOST_CONTACT_INSTALL, adminErrorText, busyLine, installBlockedReason, installConfirmLabel, installResultText,
  installableEngines, isInstallable, useDetailsOpen, type AdminBusy,
} from './diagnosticsAdmin'

type Kind = 'install' | 'upgrade'
type Outcome =
  | { kind: Kind; name: string; ok: boolean; output: string[] }
  | { kind: Kind; name: string; error: unknown }

/**
 * "Packages": optional packages with Install… / Upgrade… (PC only). Install
 * and upgrade are synchronous on the server (no progress, no cancel), so the
 * request stays open and every admin button on the page waits for it.
 */
export function PackagesSection({ overview, pc, jobsActive, busy, onBusy, onChanged, onOpenChange }: {
  overview: DiagnosticsOverview
  pc: PcMode
  jobsActive: boolean
  busy: AdminBusy
  onBusy: (b: AdminBusy) => void
  // An install or upgrade finished: refetch the overview and setup checks.
  onChanged: () => void
  onOpenChange: (open: boolean) => void
}) {
  const [openRef, open] = useDetailsOpen()
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const reasonId = useId()
  const runningId = useId()
  // Install/Upgrade only once /api/meta says this is the main PC.
  const local = pc === 'local'
  // Jobs are polled only while this is open and the buttons can be used.
  const watching = open && local
  useEffect(() => {
    onOpenChange(watching)
    return () => onOpenChange(false)
  }, [watching, onOpenChange])

  const deps = splitDependencies(overview.dependencies)
  const engines = installableEngines(overview.model_engine_versions, Object.keys(overview.dependencies))
  const blocked = installBlockedReason(jobsActive, busy)
  const running = busyLine(busy)

  const run = async (kind: Kind, name: string) => {
    onBusy({ kind, name })
    setOutcome(null)
    try {
      const r = await (kind === 'install' ? installDependency(name) : upgradeDependency(name))
      setOutcome({ kind, name, ok: r.ok, output: r.output_tail })
      if (r.ok) onChanged()
    } catch (e) {
      setOutcome({ kind, name, error: e })
    } finally {
      onBusy(null)
    }
  }

  const action = (kind: Kind, name: string) =>
    local && (
      <ConfirmButton
        name={name}
        label={kind === 'install' ? 'Install…' : 'Upgrade…'}
        verb={kind}
        tone="primary"
        confirmLabel={kind === 'install' ? installConfirmLabel(name) : undefined}
        disabled={!!blocked}
        describedBy={running ? runningId : blocked ? reasonId : undefined}
        busy={busy?.name === name && busy.kind === kind}
        onConfirm={() => void run(kind, name)}
      />
    )

  return (
    <Section title="Packages" storageKey="diagnostics.packages" summary={`${deps.installed.length} installed, ${deps.missing.length} missing.`}>
      <div ref={openRef} data-testid="dependency-panel" className="diag-stack">
        <p>
          {deps.installed.length} installed, {deps.missing.length} missing.
        </p>
        {pc === 'remote' && <p className="muted">Installing is PC only.</p>}
        {local && blocked && !running && <p className="muted" id={reasonId}>{blocked}</p>}
        <p className="muted" aria-live="polite" data-testid="install-running" id={runningId}>
          {running ?? ''}
        </p>
        {outcome && <OutcomeBlock outcome={outcome} onRecheck={onChanged} />}
        {deps.missing.length > 0 && (
          <Section storageKey="diagnostics.missing" title="Missing packages" count={deps.missing.length}>
            <ul aria-label="Missing packages" className="pkg-list">
              {deps.missing.map((d) => (
                <li key={d.name}>
                  <span>
                    <strong>{d.name}</strong> <span className="muted">{d.powers}</span>
                  </span>
                  {isInstallable(d.tier) && action('install', d.name)}
                </li>
              ))}
            </ul>
          </Section>
        )}
        {engines.length > 0 && (
          <Section storageKey="diagnostics.engines" title="Model engines not installed" count={engines.length}>
            <ul aria-label="Model engines not installed" className="pkg-list">
              {engines.map((m) => (
                <li key={m.name}>
                  <span>
                    <strong>{m.name}</strong> <span className="muted">{m.help}</span>
                  </span>
                  {action('install', m.package as string)}
                </li>
              ))}
            </ul>
          </Section>
        )}
        {deps.installed.length > 0 && (
          <Section storageKey="diagnostics.installed" title="Installed packages" count={deps.installed.length}>
            <ul aria-label="Installed packages" className="pkg-list">
              {deps.installed.map((d) => (
                <li key={d.name}>
                  <span>
                    <strong>{d.name}</strong> <span className="muted">{d.powers}</span>
                  </span>
                  {isInstallable(d.tier) && action('upgrade', d.name)}
                </li>
              ))}
            </ul>
          </Section>
        )}
      </div>
    </Section>
  )
}

function OutcomeBlock({ outcome, onRecheck }: { outcome: Outcome; onRecheck: () => void }) {
  if ('error' in outcome) {
    const text = adminErrorText(outcome.error, outcome.kind)
    return (
      <div className="diag-stack" role="alert">
        <p className="error">{text}</p>
        {text === LOST_CONTACT_INSTALL && (
          <div className="actions">
            <button type="button" onClick={onRecheck}>
              Check again
            </button>
          </div>
        )}
      </div>
    )
  }
  return <ResultBlock outcome={outcome} />
}

function ResultBlock({ outcome }: { outcome: Extract<Outcome, { ok: boolean }> }) {
  const lineRef = useRef<HTMLParagraphElement>(null)
  // Focus the result like the reset result, so it is read out and easy to find.
  // Next frame: the ConfirmButton that ran it returns focus to itself in this commit.
  useEffect(() => {
    const f = requestAnimationFrame(() => lineRef.current?.focus())
    return () => cancelAnimationFrame(f)
  }, [outcome])
  const text = installResultText(outcome.kind, outcome.name, outcome.ok)
  return (
    <div className="diag-stack" data-testid="install-result">
      <p ref={lineRef} tabIndex={-1} className={outcome.ok ? undefined : 'error'} role={outcome.ok ? 'status' : 'alert'}>
        {text}
      </p>
      {/* Keyed by the result so a new one re-applies defaultOpen. */}
      <Section key={`${outcome.kind}-${outcome.name}-${outcome.ok}`} title="Output" defaultOpen={!outcome.ok}>
        <pre className="diag-pre">{outcome.output.length ? outcome.output.join('\n') : 'No output.'}</pre>
      </Section>
    </div>
  )
}
