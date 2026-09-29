// Pure helpers (and one small hook) for the Diagnostics admin sections and
// the Settings extension section. Copy lives here so it is unit-tested.
import { useCallback, useEffect, useState } from 'react'

import type { ApiError } from '../../api/client'
import { PC_ONLY_FORBIDDEN, describeError, safeDetail } from '../../components/errorMessages'
import type {
  DiagnosticsJobHistoryItem, DiagnosticsModelCache, DiagnosticsPyannoteReadiness, DiagnosticsSetupChecks,
  GpuStatus, ModelEngineVersion,
} from '../../types/diagnostics'
import type { ExtensionEnabledResult, ExtensionStatus } from '../../types/extension'
import type { LibraryDashboard } from '../../types/library'
import { formatBytes } from '../libraryAdmin/libraryAdmin'
import { describeGpu, formatSeconds, statusLabel } from '../diagnosticsFormat'

// Mirrors diagnostics.py INSTALLABLE_TIERS: only these get Install/Upgrade.
export const INSTALLABLE_TIERS: readonly string[] = ['feature', 'engine']
export const isInstallable = (tier: string) => INSTALLABLE_TIERS.includes(tier)

// Big downloads get their size in the confirm step.
const LARGE_PACKAGES: Record<string, string> = { torch: 'about 2.5 GB', torchaudio: 'about 2.5 GB' }
export const installConfirmLabel = (name: string): string | undefined =>
  LARGE_PACKAGES[name] ? `Confirm install ${name} (${LARGE_PACKAGES[name]})` : undefined

// ---- page-wide busy state ----

export type AdminAction = 'install' | 'upgrade' | 'reset'
export type AdminBusy = { kind: AdminAction; name: string } | null

/** Why Install/Upgrade can't run now, or null. */
export function installBlockedReason(jobsActive: boolean, busy: AdminBusy): string | null {
  if (busy?.kind === 'reset') return 'Wait for the reset to finish.'
  if (busy) return 'Wait for the install to finish.'
  if (jobsActive) return 'Wait for running jobs to finish before installing.'
  return null
}

/** Why Reset library can't run now, or null. */
export function resetBlockedReason(jobsActive: boolean, busy: AdminBusy): string | null {
  if (busy && busy.kind !== 'reset') return 'Wait for the install to finish.'
  if (jobsActive) return 'Stop running jobs first (see Jobs above).'
  return null
}

/** The aria-live line while an install or upgrade runs. */
export function busyLine(busy: AdminBusy): string | null {
  if (!busy || busy.kind === 'reset') return null
  const verb = busy.kind === 'install' ? 'Installing' : 'Upgrading'
  return `${verb} ${busy.name}… this can take several minutes. Keep this tab open.`
}

export function installResultText(kind: 'install' | 'upgrade', name: string, ok: boolean): string {
  if (!ok) return `${kind === 'install' ? 'Install' : 'Upgrade'} failed for ${name}.`
  return kind === 'install' ? `Installed ${name}.` : `Upgraded ${name}. Restart Baihe to load the new version.`
}

export const LOST_CONTACT_INSTALL =
  'Lost contact while installing. It may still be running on the PC; check again in a few minutes.'

/**
 * One plain sentence for a failed admin call. 403: PC only. 404 (install):
 * unknown package. 409/422: the server's own sentence (fixed text, filtered
 * by safeDetail). A dropped connection during an install gets its own copy.
 */
export function adminErrorText(err: unknown, action: AdminAction): string {
  const e = err as Partial<ApiError> | null
  const status = e?.status
  if (status === 0 && action !== 'reset') return LOST_CONTACT_INSTALL
  if (status === 403 || e?.code === 'forbidden') return PC_ONLY_FORBIDDEN
  if (status === 404 && action !== 'reset') return 'Unknown or non-installable package.'
  if (status === 409 || status === 422) {
    const own = e?.message ? safeDetail(e.message) : null
    if (own) return own
  }
  return describeError(err, { pcOnly: true, serverText: true }).title
}

// ---- Setup ----

export type SetupRow = { key: string; label: string; text: string; problem: boolean }

/** The Setup rows ("Label: value", or "Problem: …") from setup-checks plus the overview's GPU. */
export function setupRows(c: DiagnosticsSetupChecks, gpu: GpuStatus | null): SetupRow[] {
  const rows: SetupRow[] = []
  const add = (key: string, label: string, ok: boolean, good: string, bad: string) =>
    rows.push({ key, label, problem: !ok, text: ok ? `${label}: ${good}` : `Problem: ${bad}` })
  add('python', 'Python', c.python.ok, c.python.version ?? 'found',
    c.python.version ? `Python ${c.python.version} is too old` : 'Python version unknown')
  add('ffmpeg', 'ffmpeg', c.ffmpeg.found, c.ffmpeg.version ?? 'found', 'ffmpeg not found')
  add('js', 'JS runtime', c.js_runtime.found, c.js_runtime.name ?? 'found',
    'no JS runtime (some video sites lose formats)')
  const gpuBlind = c.cuda.torch_installed && c.cuda.cuda_available === false
  if (gpu || gpuBlind) add('gpu', 'GPU', !gpuBlind, gpu ? describeGpu(gpu) : '', "PyTorch can't see the GPU")
  const missing = c.files.missing_top_level.length + c.files.missing_tabs.length
  add('files', 'App files', missing === 0, 'all present', `${missing} missing`)
  add('library', 'Library folder', c.library_writable, 'writable', "can't be written to")
  return rows
}

/** "All 6 OK" or "2 problems: ffmpeg, JS runtime". */
export function setupSummary(rows: SetupRow[]): string {
  const bad = rows.filter((r) => r.problem)
  if (!bad.length) return `All ${rows.length} OK`
  return `${bad.length} ${bad.length === 1 ? 'problem' : 'problems'}: ${bad.map((r) => r.label).join(', ')}`
}

/** One "‹name›: ‹version›" row per model engine. */
export const engineRow = (m: ModelEngineVersion) =>
  `${m.name}: ${m.installed ? (m.version ?? 'installed') : 'not installed'}`

/** Model engines with a pip package that aren't installed, minus names already listed as packages. */
export function installableEngines(engines: ModelEngineVersion[], packageNames: string[]) {
  const seen = new Set(packageNames)
  return engines.filter((m) => !m.installed && m.package && !seen.has(m.package))
}

/** Header line parts: setup ("Setup OK" / "2 setup problems"), packages, jobs (only when running). */
export function headerParts(setupProblems: number | null, installed: number | null, total: number | null, running: number) {
  const setup = setupProblems == null ? null
    : setupProblems === 0 ? 'Setup OK'
      : `${setupProblems} setup ${setupProblems === 1 ? 'problem' : 'problems'}`
  const rest: string[] = []
  if (installed != null && total != null) rest.push(`${installed} of ${total} packages`)
  if (running > 0) rest.push(`${running} ${running === 1 ? 'job' : 'jobs'} running`)
  return { setup, warn: !!setupProblems, rest: rest.join(' · ') }
}

// ---- Speaker detection ----

export function pyannoteSummary(r: DiagnosticsPyannoteReadiness): string {
  if (r.ready) return 'Ready'
  if (!r.pyannote_installed) return 'Not ready: pyannote missing'
  if (!r.hf_token_configured) return 'Not ready: no Hugging Face token'
  return 'Not ready: a model needs its terms accepted'
}

export const hfModelUrl = (model: string) => `https://huggingface.co/${model.split('/').map(encodeURIComponent).join('/')}`

// ---- Model cache ----

export const hasModelCache = (c: DiagnosticsModelCache | null) =>
  !!c && (c.hf_cache.length > 0 || c.piper_voices.length > 0)

/** "12.4 GB · 7 models · 2 voices". */
export function modelCacheSummary(c: DiagnosticsModelCache): string {
  const parts = [formatBytes(c.hf_total_bytes + c.piper_total_bytes)]
  if (c.hf_cache.length) parts.push(`${c.hf_cache.length} ${c.hf_cache.length === 1 ? 'model' : 'models'}`)
  if (c.piper_voices.length) parts.push(`${c.piper_voices.length} ${c.piper_voices.length === 1 ? 'voice' : 'voices'}`)
  return parts.join(' · ')
}

// ---- Job history ----

/** "Translate · Done · 3m 05s · GPU". */
export function historySummary(h: DiagnosticsJobHistoryItem): string {
  const parts = [h.label || h.description || h.job_id]
  if (h.status) parts.push(statusLabel(h.status))
  if (h.duration_seconds != null) parts.push(formatSeconds(h.duration_seconds))
  if (h.gpu_touching) parts.push('GPU')
  return parts.join(' · ')
}

export const HISTORY_PAGE = 20

// ---- Log and report ----

export const LOG_LINE_CHOICES = [50, 100, 200] as const
export const LOG_DEBOUNCE_MS = 300
export const logEmptyText = (keyword: string) => (keyword.trim() ? 'No lines match.' : 'Nothing logged yet.')
export const copyFallbackText = (touch: boolean) => (touch ? 'Long-press to copy.' : 'Press Ctrl+C to copy.')
export const COPIED_MS = 2000

// ---- Danger zone ----

/** "Currently 12 dramas, 48,210 lines." or null when the library is empty. */
export function libraryStatsLine(s: Pick<LibraryDashboard, 'total_dramas' | 'total_lines'>): string | null {
  if (s.total_dramas <= 0) return null
  const n = s.total_dramas
  return `Currently ${n.toLocaleString('en-US')} ${n === 1 ? 'drama' : 'dramas'}, ` +
    `${s.total_lines.toLocaleString('en-US')} ${s.total_lines === 1 ? 'line' : 'lines'}.`
}

// ---- Browser extension (Settings) ----

export function extensionSummary(s: ExtensionStatus): string {
  if (s.enabled) return s.running ? 'On · running' : 'On · starts next time Baihe starts'
  return s.running ? 'Off · still running until Baihe restarts' : 'Off'
}

/** The note after a toggle, or null. */
export function extensionToggleNote(r: ExtensionEnabledResult): string | null {
  if (!r.enabled && (r.restart_needed || r.running)) return 'Off. Restart Baihe to stop it now.'
  if (r.enabled && !r.running) return 'On. It starts next time Baihe starts.'
  return null
}

export const TOKEN_VISIBLE_MS = 120_000

// ---- hooks ----

/**
 * Tracks whether the <details> around an element is open, for sections that
 * load or poll only while open. Section (components/) doesn't expose its
 * state, so attach the returned ref to any element inside its body.
 */
export function useDetailsOpen(): [(el: HTMLElement | null) => void, boolean] {
  const [details, setDetails] = useState<HTMLDetailsElement | null>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!details) return
    const sync = () => setOpen(details.open)
    sync()
    details.addEventListener('toggle', sync)
    return () => details.removeEventListener('toggle', sync)
  }, [details])
  // Stable, so React doesn't detach and re-attach it on every render.
  const ref = useCallback((el: HTMLElement | null) => {
    if (el) setDetails(el.closest('details'))
  }, [])
  return [ref, open]
}
