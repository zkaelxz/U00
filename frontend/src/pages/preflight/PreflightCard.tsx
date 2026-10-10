import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../../api/client'
import { getDiagnostics, getGpuTorch, getInstallPresets, getSetupChecks, installDependency, setupGpuTorch } from '../../api/diagnostics'
import { listJobs } from '../../api/jobs'
import { translateApi } from '../../api/translate'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import { isActive } from '../diagnosticsFormat'
import { installBlockedReason, type AdminBusy } from '../diagnostics/diagnosticsAdmin'
import { InstallProgress } from '../diagnostics/InstallProgress'
import { runInstallJob } from '../diagnostics/installJob'
import { formatApproxMb, missingTranscription, taskConfirmLabel } from '../diagnostics/installPresets'
import { SettingsKeyForm } from '../SettingsKeyForm'
import { engineLabel } from '../../labels'
import {
  DENIED_NOTE, alternativeEngine, preflightReady, preflightRows,
  type PreflightInputs, type PreflightNeed, type PreflightRow, type Read,
} from './preflightModel'
import './preflight.css'

const GPU_SIZE = 'about 2.5 GB'

const PENDING: Read<never> = { data: null, denied: false, failed: false }
const NOT_NEEDED = PENDING

async function read<T>(load: () => Promise<T>): Promise<Read<T>> {
  try {
    return { data: await load(), denied: false, failed: false }
  } catch (e) {
    const denied = (e as Partial<ApiError> | null)?.status === 403
    return { data: null, denied, failed: !denied }
  }
}

type Loaded = PreflightInputs & { jobsActive: boolean; nvidia: boolean; done: boolean }

const EMPTY: Loaded = {
  presets: NOT_NEEDED, setup: NOT_NEEDED, gpu: NOT_NEEDED, engines: NOT_NEEDED, jobsActive: false, nvidia: false, done: false,
}

/**
 * What is missing before a run, with the fix inline. It renders nothing when
 * every row is OK. Without admin.diagnostics (a 403) or away from the main PC
 * the rows are read-only text: installs, GPU setup and key writes are PC only.
 * `whisperInstalled` is the stage's own answer and is used when the presets
 * can't be read; `onReady(true)` fires when nothing blocking is left.
 */
export function PreflightCard({ needs, engine, whisperInstalled, onReady, onUseEngine }: {
  needs: PreflightNeed[]
  engine?: string
  whisperInstalled?: boolean
  onReady?: (ok: boolean, blockers: PreflightRow[]) => void
  /** Switch to another translator (the caller owns where that choice is stored). */
  onUseEngine?: (name: string) => void
}) {
  const pc = usePcOnly()
  const [loaded, setLoaded] = useState<Loaded>(EMPTY)
  const [tick, setTick] = useState(0)
  const [busy, setBusy] = useState<AdminBusy>(null)
  const [failure, setFailure] = useState<unknown>(null)
  const retry = useRef<(() => void) | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [keyOpen, setKeyOpen] = useState(false)
  const needKey = needs.join()

  useEffect(() => {
    let cancelled = false
    const want = (n: PreflightNeed) => needKey.split(',').includes(n)
    void (async () => {
      const [presets, setup, diag, engines, jobs, torch] = await Promise.all([
        want('whisper') ? read(getInstallPresets) : NOT_NEEDED,
        want('ffmpeg') || want('gpu') ? read(getSetupChecks) : NOT_NEEDED,
        want('gpu') ? read(getDiagnostics) : NOT_NEEDED,
        want('key') ? read(translateApi.engines) : NOT_NEEDED,
        want('whisper') || want('gpu') ? read(listJobs) : NOT_NEEDED,
        want('gpu') ? read(getGpuTorch) : NOT_NEEDED,
      ])
      if (cancelled) return
      setLoaded({
        presets, setup, engines, gpu: { data: diag.data?.gpu ?? null, denied: diag.denied, failed: diag.failed },
        // A failed jobs read must not lock the install button; the server still refuses a clash.
        jobsActive: !!jobs.data?.items?.some((j) => isActive(j.status)),
        nvidia: !!torch.data?.nvidia.found, done: true,
      })
    })()
    return () => { cancelled = true }
  }, [needKey, tick])

  const inputs: PreflightInputs = { ...loaded, engine, whisperInstalled }
  const rows = loaded.done ? preflightRows(needs, inputs) : []
  const ready = preflightReady(rows)
  const lastReady = useRef<boolean | null>(null)
  useEffect(() => {
    if (!loaded.done || lastReady.current === ready) return
    lastReady.current = ready
    onReady?.(ready, rows.filter((r) => r.blocking))
  }, [loaded.done, ready, onReady])

  const recheck = useCallback(() => setTick((t) => t + 1), [])
  const blocked = installBlockedReason(loaded.jobsActive, busy)
  const task = missingTranscription(loaded.presets.data)

  const install = async (label: string, run: (onJob: (job: NonNullable<AdminBusy>['job']) => void) => Promise<{ ok: boolean; cancelled?: boolean; hint?: string | null }>) => {
    retry.current = () => void install(label, run)
    setFailure(null)
    setNote(null)
    setBusy({ kind: 'install', name: label })
    try {
      const r = await run((job) => setBusy({ kind: 'install', name: label, job }))
      if (r.cancelled) setNote(`Cancelled installing ${label}.`)
      else if (!r.ok) setFailure(new ApiError(500, { code: 'dependency_unavailable', message: r.hint ?? '' }))
      recheck()
    } catch (e) {
      setFailure(e)
    } finally {
      setBusy(null)
    }
  }

  const installWhisper = () => install('transcription', async (onJob) => {
    // One package at a time (the server runs one pip); the first failure ends the run.
    let last = { ok: true } as Awaited<ReturnType<typeof runInstallJob>>
    for (const pkg of task?.to_install ?? []) {
      last = await runInstallJob(() => installDependency(pkg), onJob, pkg)
      if (!last.ok) break
    }
    return last
  })
  const installGpu = () => install('GPU PyTorch', (onJob) => runInstallJob(() => setupGpuTorch('cu128'), onJob, 'GPU PyTorch'))

  if (!rows.length) return null
  const readOnly = pc === 'remote'

  const action = (r: PreflightRow) => {
    if (readOnly || r.noFix) return <p className="muted preflight-ask">{DENIED_NOTE}</p>
    if (r.need === 'whisper') {
      if (!task) return <ButtonLink variant="secondary" size="sm" href="#/diagnostics?install=transcription">Open Diagnostics</ButtonLink>
      const size = formatApproxMb(task.approx_mb)
      return (
        <ConfirmButton
          name="transcription" verb="install" tone="primary"
          label={size ? `Install (${size.replace('approx.', 'about')})…` : 'Install…'}
          confirmLabel={taskConfirmLabel(task)}
          busy={!!busy} disabled={!!blocked} onConfirm={installWhisper}
        />
      )
    }
    if (r.need === 'ffmpeg') {
      return <button type="button" className={buttonClass('secondary')} onClick={recheck}>Check again</button>
    }
    if (r.need === 'gpu') {
      if (!loaded.nvidia) return null
      return (
        <ConfirmButton
          name="GPU PyTorch" verb="set up" tone="primary" label="Set up GPU…"
          confirmLabel={`Confirm GPU setup (${GPU_SIZE})`}
          busy={!!busy} disabled={!!blocked} onConfirm={installGpu}
        />
      )
    }
    const other = onUseEngine ? alternativeEngine(loaded.engines.data, engine) : null
    return (
      <div className="preflight-actions">
        <button type="button" className={buttonClass('secondary')} aria-expanded={keyOpen} onClick={() => setKeyOpen((o) => !o)}>
          Add key
        </button>
        {other && (
          <button type="button" className={buttonClass('ghost')} onClick={() => onUseEngine?.(other)}>
            Use another translator ({engineLabel(other)})
          </button>
        )}
      </div>
    )
  }

  const keyEngine = loaded.engines.data?.find((e) => e.name === engine)
  return (
    <Card title="Before you run" className="preflight" aria-label="Before you run" as="section">
      <ul className="preflight-rows">
        {rows.map((r) => (
          <li key={r.need} className="preflight-row" data-testid={`preflight-${r.need}`}>
            <strong className="preflight-label">{r.label}</strong>
            <span className="preflight-state">
              <Badge tone={r.tone}>{r.badge}</Badge> {r.state}
            </span>
            <div className="preflight-action">
              {action(r)}
              {r.need === 'ffmpeg' && !readOnly && !r.noFix && (
                <p className="muted">Install FFmpeg, make sure it is on your PATH, then check again.</p>
              )}
            </div>
            {r.need === 'key' && keyOpen && !readOnly && keyEngine && (
              <div className="preflight-key">
                <SettingsKeyForm engine={keyEngine.name} label={engineLabel(keyEngine.name)} configured={keyEngine.key_configured}
                  onResult={() => { setKeyOpen(false); recheck() }} />
              </div>
            )}
          </li>
        ))}
      </ul>
      {busy && (busy.job
        ? <InstallProgress job={busy.job} name={busy.name} />
        : <p className="muted" role="status">Starting the install…</p>)}
      {!busy && blocked && !readOnly && rows.some((r) => r.need === 'whisper' || r.need === 'gpu') && (
        <p className="muted" role="note">{blocked}</p>
      )}
      {note && <p className="muted" role="status">{note}</p>}
      <ErrorBanner error={failure} describe={{ pcOnly: true, serverText: true }} />
      {!!failure && !busy && (
        <div className="actions">
          <button type="button" className={buttonClass('secondary')} onClick={() => retry.current?.()}>
            Try again
          </button>
        </div>
      )}
    </Card>
  )
}
