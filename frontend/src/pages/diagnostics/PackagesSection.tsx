import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'

import { checkPackageUpdates, getInstallPresets, installDependency, setupGpuTorch, upgradeDependency } from '../../api/diagnostics'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Section } from '../../components/Section'
import { usePcPendingNote, type PcMode } from '../../hooks/usePcOnly'
import type {
  DiagnosticsInstallPresets, DiagnosticsInstallTask, DiagnosticsOverview, DiagnosticsPackageInfo, DiagnosticsPackageUpdate,
  DiagnosticsPackageUpdates, DiagnosticsTorchVariant,
} from '../../types/diagnostics'
import { splitDependencies } from '../diagnosticsFormat'
import {
  LOST_CONTACT_INSTALL, adminErrorText, busyLine, installBlockedReason, installConfirmLabel, installResultText,
  installableEngines, isInstallable, useDetailsOpen, type AdminBusy,
} from './diagnosticsAdmin'
import { GpuTorchPanel } from './GpuTorchPanel'
import { setupConfirmLabel, verifyText } from './gpuTorch'
import { canUpdate, updateLine, updatesSummary, versionLabel } from './packageUpdates'
import {
  belowMinText, firstHint, groupTasks, minVersionText, optionalMissingText, packageSizeText, roleLabel, safeSourceUrl,
  sortTasksNeedingInstall, taskConfirmLabel, taskNotes, taskOutput, taskReady, taskResultText, taskStatus,
  type TaskRunResult,
} from './installPresets'

type Kind = 'install' | 'upgrade'
type Outcome =
  // text: a task install's own summary line (otherwise installResultText).
  | { kind: Kind; name: string; ok: boolean; output: string[]; hint?: string | null; text?: string }
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
  const pending = usePcPendingNote(pc)
  // Jobs are polled only while this is open and the buttons can be used.
  const watching = open && local
  useEffect(() => {
    onOpenChange(watching)
    return () => onOpenChange(false)
  }, [watching, onOpenChange])

  // Presets are optional extras (sizes, links, tasks); the lists work without them.
  const [presets, setPresets] = useState<DiagnosticsInstallPresets | null>(null)
  const loadPresets = useCallback(() => {
    getInstallPresets().then(setPresets, () => undefined)
  }, [])
  useEffect(() => loadPresets(), [loadPresets])
  const [gpuKey, setGpuKey] = useState(0)
  const changed = () => {
    onChanged()
    loadPresets()
    setGpuKey((k) => k + 1)
  }
  const info = (name: string): DiagnosticsPackageInfo | undefined => presets?.packages[name]
  const torchInstalled = !!overview.dependencies.torch?.installed

  const deps = splitDependencies(overview.dependencies)
  const engines = installableEngines(overview.model_engine_versions, Object.keys(overview.dependencies))
  const blocked = installBlockedReason(jobsActive, busy)
  const running = busyLine(busy)

  // "Check for updates": PyPI is asked (on the server) only when pressed.
  const [updates, setUpdates] = useState<DiagnosticsPackageUpdates | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState<string | null>(null)
  const checkUpdates = async () => {
    setChecking(true)
    setCheckError(null)
    try {
      setUpdates(await checkPackageUpdates())
    } catch (e) {
      setCheckError(adminErrorText(e, 'upgrade'))
    } finally {
      setChecking(false)
    }
  }

  const run = async (kind: Kind, name: string, target?: string) => {
    onBusy({ kind, name })
    setOutcome(null)
    try {
      const r = await (kind === 'install' ? installDependency(name) : upgradeDependency(name, target ?? ''))
      setOutcome({ kind, name, ok: r.ok, output: r.output_tail, hint: r.hint })
      // An install or update can move other packages too: the server dropped its check, so does this.
      setUpdates(null)
      if (r.ok) changed()
    } catch (e) {
      setOutcome({ kind, name, error: e })
    } finally {
      onBusy(null)
    }
  }

  // GPU PyTorch: the matched torch/torchvision/torchaudio set from the server's table.
  const GPU_NAME = 'GPU PyTorch'
  const runGpuSetup = async (v: DiagnosticsTorchVariant) => {
    onBusy({ kind: 'install', name: `${v.needs_nvidia ? 'GPU' : 'CPU'} PyTorch (about ${v.needs_nvidia ? '2.5 GB' : '300 MB'})` })
    setOutcome(null)
    try {
      const r = await setupGpuTorch(v.variant)
      setUpdates(null)
      const check = r.verify ? ` ${verifyText(r.verify)}` : ''
      setOutcome({
        kind: 'install', name: GPU_NAME, ok: r.ok, output: r.output_tail, hint: r.hint,
        text: (r.ok ? 'PyTorch is set up. Restart Baihe to load it.' : 'PyTorch setup failed.') + check,
      })
      changed()
    } catch (e) {
      setOutcome({ kind: 'install', name: GPU_NAME, error: e })
    } finally {
      onBusy(null)
    }
  }

  // "Install for this task": one package at a time (the server runs one pip
  // at a time); a failure doesn't stop the rest.
  const [taskRunning, setTaskRunning] = useState<string | null>(null)
  const runTask = async (t: DiagnosticsInstallTask) => {
    setOutcome(null)
    setTaskRunning(t.id)
    const results: TaskRunResult[] = []
    setUpdates(null)
    try {
      for (const [i, name] of t.to_install.entries()) {
        onBusy({ kind: 'install', name: `${name} (${i + 1} of ${t.to_install.length})` })
        try {
          const r = await installDependency(name)
          results.push({ name, ok: r.ok, output: r.output_tail, hint: r.hint })
        } catch (e) {
          if (!results.length) {
            setOutcome({ kind: 'install', name: t.label, error: e })
            return
          }
          results.push({ name, ok: false, output: [adminErrorText(e, 'install')] })
        }
      }
      const ok = results.every((r) => r.ok)
      setOutcome({
        kind: 'install', name: t.label, ok, output: taskOutput(results), hint: firstHint(results),
        text: taskResultText(t.label, results),
      })
    } finally {
      onBusy(null)
      setTaskRunning(null)
      if (results.some((r) => r.ok)) changed()
    }
  }

  const action = (kind: Kind, name: string, target?: string) =>
    local && (
      <ConfirmButton
        name={name}
        label={kind === 'install' ? 'Install…' : `Update to ${target}…`}
        ariaLabel={kind === 'install' ? undefined : `Update ${name} to ${target}`}
        verb={kind === 'install' ? 'install' : 'update'}
        tone="primary"
        confirmLabel={kind === 'install' ? installConfirmLabel(name) : `Confirm update ${name} to ${target}`}
        disabled={!!blocked}
        describedBy={running ? runningId : blocked ? reasonId : undefined}
        busy={busy?.name === name && busy.kind === kind}
        onConfirm={() => void run(kind, name, target)}
      />
    )

  return (
    <Section title="Packages" storageKey="diagnostics.packages" summary={`${deps.installed.length} installed, ${deps.missing.length} missing.`}>
      <div ref={openRef} data-testid="dependency-panel" className="diag-stack">
        <p>
          {deps.installed.length} installed, {deps.missing.length} missing.
        </p>
        {pc === 'remote' && <p className="muted">Installing is PC only.</p>}
        {pending && <p className="muted" data-testid="pc-pending">{pending}</p>}
        {local && blocked && !running && <p className="muted" id={reasonId}>{blocked}</p>}
        <p className="muted" aria-live="polite" data-testid="install-running" id={runningId}>
          {running ?? ''}
        </p>
        {outcome && <OutcomeBlock outcome={outcome} onRecheck={changed} />}
        <GpuTorchPanel refreshKey={gpuKey} action={(v, reason) => local && (
          <ConfirmButton
            name={GPU_NAME}
            label={v.needs_nvidia ? 'Set up GPU PyTorch…' : 'Set up PyTorch (CPU)…'}
            ariaLabel={v.needs_nvidia ? 'Set up GPU PyTorch' : 'Set up PyTorch (CPU)'}
            verb="install"
            tone="primary"
            confirmLabel={setupConfirmLabel(v)}
            disabled={!!blocked || !!reason}
            describedBy={running ? runningId : blocked ? reasonId : undefined}
            busy={busy?.name.includes('PyTorch (about') ?? false}
            onConfirm={() => void runGpuSetup(v)}
          />
        )} />
        {presets && presets.tasks.length > 0 && (
          <Section storageKey="diagnostics.tasks" title="Install by task"
            summary="Pick what you want to do; only its packages are installed.">
            <div className="diag-stack" data-testid="install-tasks">
              {groupTasks(sortTasksNeedingInstall(presets.tasks)).map((g) => (
                <div key={g.group} className="diag-stack">
                  <h4 className="task-group">{g.group}</h4>
                  <ul aria-label={`${g.group} tasks`} className="pkg-list task-list">
                    {g.tasks.map((t) => (
                      <TaskRow key={t.id} task={t} packages={presets.packages}
                        action={local && t.to_install.length > 0 && (
                          <ConfirmButton
                            name={t.label}
                            label="Install for this task…"
                            ariaLabel={`Install for ${t.label}`}
                            verb="install"
                            tone="primary"
                            confirmLabel={taskConfirmLabel(t)}
                            disabled={!!blocked}
                            describedBy={running ? runningId : blocked ? reasonId : undefined}
                            busy={!!busy && taskRunning === t.id}
                            onConfirm={() => void runTask(t)}
                          />
                        )} />
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </Section>
        )}
        {deps.missing.length > 0 && (
          <Section storageKey="diagnostics.missing" title="Missing packages" count={deps.missing.length}>
            <ul aria-label="Missing packages" className="pkg-list">
              {deps.missing.map((d) => (
                <li key={d.name}>
                  <PackageText name={d.name} text={d.powers} info={info(d.name)} torchInstalled={torchInstalled} />
                  {isInstallable(d.tier) && !info(d.name)?.not_offered_reason && action('install', d.name)}
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
                  <PackageText name={m.name} text={m.help ?? ''} info={info(m.package as string)} torchInstalled={torchInstalled} />
                  {!info(m.package as string)?.not_offered_reason && action('install', m.package as string)}
                </li>
              ))}
            </ul>
          </Section>
        )}
        {deps.installed.length > 0 && (
          <Section storageKey="diagnostics.installed" title="Installed packages" count={deps.installed.length}>
            <div className="actions" data-testid="update-check">
              <button type="button" onClick={() => void checkUpdates()} disabled={checking} aria-busy={checking}>
                {checking ? 'Checking PyPI…' : updates ? 'Check again' : 'Check for updates'}
              </button>
              <span className="muted" aria-live="polite">
                {checkError ?? (updates ? updatesSummary(updates)
                  : 'Asks PyPI for newer releases; nothing is checked until you press it.')}
              </span>
            </div>
            <ul aria-label="Installed packages" className="pkg-list">
              {deps.installed.map((d) => {
                const u = updates?.packages[d.name]
                return (
                  <li key={d.name}>
                    <PackageText name={d.name} text={d.powers} info={info(d.name)} torchInstalled={torchInstalled} installed update={u} />
                    {isInstallable(d.tier) && canUpdate(u) && action('upgrade', d.name, u.target)}
                  </li>
                )
              })}
            </ul>
          </Section>
        )}
      </div>
    </Section>
  )
}

/** Name and purpose, then approx. size, a PyPI link, and any caveat. */
function PackageText({ name, text, info, torchInstalled, installed = false, update }: {
  name: string
  text: string
  info: DiagnosticsPackageInfo | undefined
  torchInstalled: boolean
  installed?: boolean
  // The last "Check for updates" result for this package, if any.
  update?: DiagnosticsPackageUpdate
}) {
  const size = info && !installed ? packageSizeText(info, torchInstalled) : null
  const url = safeSourceUrl(info?.source_url)
  const version = installed ? versionLabel(update?.installed_version ?? info?.installed_version) : null
  const line = update ? updateLine(update) : null
  return (
    <span className="pkg-text">
      <span>
        <strong>{name}</strong>{version && <> <span className="pkg-version" data-testid="pkg-version">{version}</span></>}{' '}
        <span className="muted">{text}</span>
      </span>
      {line && <span className={line.tone === 'muted' ? 'muted' : line.tone} data-testid="pkg-update">{line.text}</span>}
      {installed && belowMinText(info) && <span className="warn" data-testid="pkg-below-min">{belowMinText(info)}</span>}
      {(size || url) && (
        <span className="pkg-meta muted">
          {size && <span data-testid="pkg-size">{size}</span>}
          {url && (
            <a className="pkg-source" href={url} target="_blank" rel="noopener noreferrer"
              aria-label={`Source: ${info?.dist ?? name} on PyPI (opens in a new tab)`}>
              Source ↗
            </a>
          )}
        </span>
      )}
      {!installed && info?.not_offered_reason && <span className="muted" data-testid="pkg-not-offered">{info.not_offered_reason}</span>}
      {!installed && !info?.not_offered_reason && info?.warning && <span className="warn">Warning: {info.warning}</span>}
    </span>
  )
}

function TaskRow({ task, packages, action }: {
  task: DiagnosticsInstallTask
  packages: Record<string, DiagnosticsPackageInfo>
  action: ReactNode
}) {
  const notes = taskNotes(task, packages)
  const optional = optionalMissingText(task)
  const size = task.to_install.length ? packageSizeText({ approx_mb: task.approx_mb, pulls_torch: false }, true) : null
  return (
    <li data-testid={`task-${task.id}`}>
      <span className="pkg-text">
        <span>
          <strong>{task.label}</strong> <span className="muted">{task.help}</span>
        </span>
        <span className="pkg-meta muted">
          <span className={taskReady(task) ? 'ok' : undefined}>{taskStatus(task)}</span>
          {size && <span>· {size} to download</span>}
        </span>
        <span className="task-pkgs muted">
          {task.packages.map((n) => {
            const role = roleLabel(task, n)
            const min = minVersionText(packages[n])
            return (
              <span key={n} data-testid={`task-pkg-${n}`}
                className={`task-pkg${packages[n]?.installed ? ' installed' : ''}${role ? ` role-${task.roles?.[n]}` : ''}`}>
                {packages[n]?.installed ? '✓ ' : ''}{n}
                {role && <span className="task-role"> · {role}</span>}
                {min && <span className="task-min"> {min}</span>}
              </span>
            )
          })}
        </span>
        {notes.map((n) => <span key={n} className="warn">{n}</span>)}
        {optional && <span className="muted" data-testid="task-optional">{optional}</span>}
      </span>
      {action}
    </li>
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
  const text = outcome.text ?? installResultText(outcome.kind, outcome.name, outcome.ok)
  return (
    <div className="diag-stack" data-testid="install-result">
      <p ref={lineRef} tabIndex={-1} className={outcome.ok ? undefined : 'error'} role={outcome.ok ? 'status' : 'alert'}>
        {text}
      </p>
      {!outcome.ok && outcome.hint && <p data-testid="install-hint">{outcome.hint}</p>}
      {/* Keyed by the result so a new one re-applies defaultOpen. */}
      <Section key={`${outcome.kind}-${outcome.name}-${outcome.ok}`} title="Output" defaultOpen={!outcome.ok}>
        <pre className="diag-pre">{outcome.output.length ? outcome.output.join('\n') : 'No output.'}</pre>
      </Section>
    </div>
  )
}
