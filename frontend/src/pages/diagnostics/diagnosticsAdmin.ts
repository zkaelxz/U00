// Pure helpers (and one small hook) for the Diagnostics admin sections and
// the Settings extension section. Copy lives here so it is unit-tested.
import { useCallback, useEffect, useState } from 'react'

import type { ApiError } from '../../api/client'
import type { BadgeTone } from '../../components/labels'
import { PC_ONLY_FORBIDDEN, describeError, safeDetail } from '../../components/errorMessages'
import { humanize } from '../../components/labels'
import type {
  DiagnosticsHfCacheEntry, DiagnosticsModelCache, DiagnosticsModelFolder, DiagnosticsPyannoteReadiness,
  DiagnosticsSetupChecks,
  GpuStatus, ModelEngineVersion,
} from '../../types/diagnostics'
import type { ExtensionEnabledResult, ExtensionEngineSettings, ExtensionStatus } from '../../types/extension'
import type { LibraryDashboard } from '../../types/library'
import { formatBytes } from '../libraryAdmin/libraryAdmin'
import { describeGpu } from '../diagnosticsFormat'

// Mirrors diagnostics.py INSTALLABLE_TIERS: only these get Install/Update.
const INSTALLABLE_TIERS: readonly string[] = ['feature', 'engine']
export const isInstallable = (tier: string) => INSTALLABLE_TIERS.includes(tier)

// Big downloads get their size in the confirm step.
const LARGE_PACKAGES: Record<string, string> = { torch: 'about 2.5 GB', torchaudio: 'about 2.5 GB' }
export const installConfirmLabel = (name: string): string | undefined =>
  LARGE_PACKAGES[name] ? `Confirm install ${name} (${LARGE_PACKAGES[name]})` : undefined

// ---- page-wide busy state ----

export type AdminAction = 'install' | 'upgrade' | 'reset'
export type AdminBusy = { kind: AdminAction; name: string } | null

/** Why Install/Update can't run now, or null. */
export function installBlockedReason(jobsActive: boolean, busy: AdminBusy): string | null {
  if (busy?.kind === 'reset') return 'Wait for the reset to finish.'
  if (busy) return 'Wait for the install to finish.'
  if (jobsActive) return 'Wait for running jobs to finish.'
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
  const verb = busy.kind === 'install' ? 'Installing' : 'Updating'
  return `${verb} ${busy.name}… this can take several minutes. Keep this tab open.`
}

export function installResultText(kind: 'install' | 'upgrade', name: string, ok: boolean): string {
  if (!ok) return `${kind === 'install' ? 'Install' : 'Update'} failed for ${name}.`
  return kind === 'install' ? `Installed ${name}.` : `Updated ${name}. Restart Baihe to load the new version.`
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

// `value` is the text without the label ("3.11.9", "ffmpeg not found"); `text` is the one-line form.
type SetupRow = { key: string; label: string; value: string; text: string; problem: boolean }

/** The ffmpeg row's problem text: missing, or built without libass. */
function ffmpegProblem(c: DiagnosticsSetupChecks): string {
  if (!c.ffmpeg.found) return 'FFmpeg not found'
  return 'FFmpeg has no libass (burned-in subtitles and the styled preview need it)'
}

const BROWSER_NAMES: Record<string, string> = {
  Chrome: 'Google Chrome', Edge: 'Microsoft Edge', custom: 'the browser named by BAIHE_BROWSER_PATH',
}

/** The Setup rows ("Label: value", or "Problem: …") from setup-checks plus the overview's GPU. */
export function setupRows(c: DiagnosticsSetupChecks, gpu: GpuStatus | null): SetupRow[] {
  const rows: SetupRow[] = []
  const add = (key: string, label: string, ok: boolean, good: string, bad: string) =>
    rows.push({ key, label, problem: !ok, value: ok ? good : bad, text: ok ? `${label}: ${good}` : `Problem: ${bad}` })
  add('python', 'Python', c.python.ok, c.python.version ?? 'found',
    c.python.version ? `Python ${c.python.version} is too old` : 'Python version unknown')
  const ffmpegOk = c.ffmpeg.found && c.ffmpeg.libass !== false
  add('ffmpeg', 'FFmpeg', ffmpegOk,
    `${c.ffmpeg.version ?? 'found'}${c.ffmpeg.libass ? ' (with libass)' : ''}`, ffmpegProblem(c))
  add('js', 'JS runtime', c.js_runtime.found, c.js_runtime.name ?? 'found',
    'no JS runtime (some video sites lose formats)')
  if (c.browser) {
    // null/undefined means the server couldn't tell; only a definite false is a problem.
    if (typeof c.browser.package === 'boolean') {
      add('playwright', 'Playwright package', c.browser.package, 'installed',
        'Playwright package not installed (add it from Diagnostics > Packages, the playwright row, or run pip install playwright; no browser download is needed when Chrome or Edge is installed)')
    }
    add('browser', 'Browser for JavaScript-only sites', c.browser.found,
      `Using ${BROWSER_NAMES[c.browser.name ?? ''] ?? c.browser.name ?? 'a browser'}`,
      'None found: install Chrome or Edge, or use Install browser support (Diagnostics > Setup)')
  }
  const gpuBlind = c.cuda.torch_installed && c.cuda.cuda_available === false
  if (gpu || gpuBlind) add('gpu', 'GPU', !gpuBlind, gpu ? describeGpu(gpu) : '', "PyTorch can't see the GPU")
  const missing = c.files.missing_top_level.length
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

type HeaderBadge = { key: string; text: string; tone: BadgeTone }

/**
 * The badge strip under the page title: setup ("Setup OK" / "2 setup problems"),
 * packages ("22 of 25 packages"), jobs ("No jobs running" / "1 job running"),
 * and a running install. A part whose data hasn't loaded is left out.
 */
export function headerBadges(
  setupProblems: number | null, installed: number | null, total: number | null, running: number | null, busy: AdminBusy = null,
): HeaderBadge[] {
  const out: HeaderBadge[] = []
  if (setupProblems != null) {
    out.push(setupProblems === 0
      ? { key: 'setup', text: 'Setup OK', tone: 'ok' }
      : { key: 'setup', text: `${setupProblems} setup ${setupProblems === 1 ? 'problem' : 'problems'}`, tone: 'warn' })
  }
  if (installed != null && total != null) out.push({ key: 'packages', text: `${installed} of ${total} packages`, tone: 'neutral' })
  if (running != null) {
    out.push(running > 0
      ? { key: 'jobs', text: `${running} ${running === 1 ? 'job' : 'jobs'} running`, tone: 'info' }
      : { key: 'jobs', text: 'No jobs running', tone: 'neutral' })
  }
  if (busy && busy.kind !== 'reset') {
    out.push({ key: 'busy', text: `${busy.kind === 'install' ? 'Installing' : 'Updating'} ${busy.name}`, tone: 'info' })
  }
  return out
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
  !!c && (c.hf_cache.length > 0 || c.model_files.length > 0)

export const MODEL_FOLDER_LABELS: Record<DiagnosticsModelFolder, string> = {
  torch: 'PyTorch hub',
  audio_separator: 'Vocal separation',
}

// Downloaded weights have no link to an engine in the API, so a Hugging Face repo is matched to
// its engine by name. Engines listed here are known to download weights; unmatched repos stay
// in their own "Other downloaded models" group instead of being guessed at.
const ENGINE_REPO_HINTS: Record<string, RegExp> = {
  'Whisper (faster-whisper)': /whisper/i,
  'Qwen3-ASR': /qwen/i,
  'SenseVoice (FunASR)': /sensevoice|funasr|funaudio/i,
  OmniVoice: /omnivoice/i,
  'manga-ocr': /manga-?ocr/i,
}

/** A "repo" engine (not a pip package) lists its Hugging Face repos, comma separated, as its version. */
const exactRepos = (e: ModelEngineVersion): string[] =>
  e.package === null && e.version?.includes('/') ? e.version.split(',').map((r) => r.trim()) : []

export type EngineModelRow = {
  engine: ModelEngineVersion
  cached: DiagnosticsHfCacheEntry[]
  // The engine downloads weights but none are cached (and it is installed): "not downloaded".
  notDownloaded: boolean
}

/**
 * One row per model engine with the Hugging Face downloads that belong to it, plus the
 * downloads that match no engine. Every cached revision lands in exactly one place.
 */
export function reconcileModels(engines: ModelEngineVersion[], hf: DiagnosticsHfCacheEntry[]) {
  const left = [...hf]
  const rows: EngineModelRow[] = engines.map((engine) => {
    const repos = exactRepos(engine)
    const hint = ENGINE_REPO_HINTS[engine.name]
    const takes = (e: DiagnosticsHfCacheEntry) => repos.includes(e.repo_id) || (!!hint && hint.test(e.repo_id))
    const cached = left.filter(takes)
    for (const c of cached) left.splice(left.indexOf(c), 1)
    return { engine, cached, notDownloaded: engine.installed && cached.length === 0 && (repos.length > 0 || !!hint) }
  })
  return { rows, other: left }
}

/** "12.4 GB · 7 models · 3 model files". */
export function modelCacheSummary(c: DiagnosticsModelCache): string {
  const parts = [formatBytes(c.hf_total_bytes + c.model_files_total_bytes)]
  if (c.hf_cache.length) parts.push(`${c.hf_cache.length} ${c.hf_cache.length === 1 ? 'model' : 'models'}`)
  if (c.model_files.length) {
    parts.push(`${c.model_files.length} ${c.model_files.length === 1 ? 'model file' : 'model files'}`)
  }
  return parts.join(' · ')
}

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
  if (!r.enabled && (r.restart_needed || r.running)) return 'Off, but it could not be stopped. Restart Baihe to stop it.'
  if (!r.enabled) return "Off. The extension can't reach Baihe now."
  if (r.enabled && !r.running) return 'On. It starts next time Baihe starts.'
  return null
}

/** The line under the engine picker: what the extension will do with a page. */
export function extensionEngineNote(s: ExtensionEngineSettings): string {
  if (!s.engine) return 'No engine: pages come back with their original text only.'
  const name = s.engines.find((e) => e.name === s.engine)?.label || humanize('engine', s.engine)
  if (!s.ready) return `No ${name} key is saved on this PC, so pages come back untranslated.`
  return `Pages are translated with ${name}. The key stays on this PC.`
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
