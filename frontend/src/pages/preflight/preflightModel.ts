// What the preflight card shows before a run: one row per thing the run
// needs, built from the diagnostics reads the card makes. Pure of React.
import { engineLabel } from '../../labels'
import type { BadgeTone } from '../../components/labels'
import type { DiagnosticsInstallPresets, DiagnosticsSetupChecks, GpuStatus } from '../../types/diagnostics'
import type { TranslateEngine } from '../../types/translate'
import { formatApproxMb, missingTranscription } from '../diagnostics/installPresets'

export type PreflightNeed = 'whisper' | 'ffmpeg' | 'gpu' | 'key'

/** One diagnostics read: its data, or that it was refused (403) or failed. */
export type Read<T> = { data: T | null; denied: boolean; failed: boolean }

export interface PreflightInputs {
  presets: Read<DiagnosticsInstallPresets>
  setup: Read<DiagnosticsSetupChecks>
  gpu: Read<GpuStatus>
  engines: Read<TranslateEngine[]>
  engine?: string
  /** The stage's own answer (config.whisper_installed); used when the presets can't be read. */
  whisperInstalled?: boolean
}

export interface PreflightRow {
  need: PreflightNeed
  label: string
  state: string
  tone: BadgeTone
  badge: string
  /** Still blocks the run (GPU is informational and never does). */
  blocking: boolean
  /** Why the fix isn't offered on this device, or null when it can be. */
  noFix: 'denied' | null
}

const row = (need: PreflightNeed, label: string, state: string, tone: BadgeTone, badge: string, blocking: boolean): PreflightRow =>
  ({ need, label, state, tone, badge, blocking, noFix: null })

function whisperRow(i: PreflightInputs): PreflightRow | null {
  const label = 'Transcription (Whisper)'
  const installed = i.presets.data?.packages.faster_whisper?.installed ?? i.whisperInstalled
  if (installed !== false) return null
  const size = formatApproxMb(missingTranscription(i.presets.data)?.approx_mb)
  return row('whisper', label, `Not installed${size ? `, ${size} download` : ''}.`, 'warn', 'Missing', true)
}

function ffmpegRow(i: PreflightInputs): PreflightRow | null {
  const found = i.setup.data?.ffmpeg.found
  if (found !== false) return null
  return row('ffmpeg', 'FFmpeg', 'Not found. Baihe needs it to read audio and video.', 'warn', 'Missing', true)
}

function gpuRow(i: PreflightInputs): PreflightRow | null {
  const usable = i.gpu.data?.available || i.setup.data?.cuda.cuda_available
  if (usable || (!i.gpu.data && !i.setup.data)) return null
  return row('gpu', 'GPU', 'Transcription will be slower on CPU.', 'info', 'No GPU in use', false)
}

function keyRow(i: PreflightInputs): PreflightRow | null {
  const e = i.engines.data?.find((x) => x.name === i.engine)
  if (!e || e.key_configured) return null
  return row('key', `${engineLabel(e.name)} key`, `No ${engineLabel(e.name)} key is saved, so it can't translate yet.`, 'warn', 'Missing', true)
}

const BUILDERS: Record<PreflightNeed, (i: PreflightInputs) => PreflightRow | null> = {
  whisper: whisperRow, ffmpeg: ffmpegRow, gpu: gpuRow, key: keyRow,
}

/** Rows that aren't OK, in the order the caller asked. Empty means nothing to show. */
export function preflightRows(needs: PreflightNeed[], i: PreflightInputs): PreflightRow[] {
  const rows = needs.flatMap((n) => BUILDERS[n](i) ?? [])
  // Installing, GPU setup and key writes need admin.diagnostics and the main PC; viewers without them get text only.
  const denied = i.presets.denied || i.setup.denied || i.gpu.denied
  return rows.map((r) => (denied && (r.need === 'whisper' || r.need === 'ffmpeg' || r.need === 'gpu') ? { ...r, noFix: 'denied' } : r))
}

/** True when nothing that blocks the run is missing. */
export const preflightReady = (rows: PreflightRow[]): boolean => !rows.some((r) => r.blocking)

/** Another translator the user can switch to right now: Ollama, when it can run. */
export function alternativeEngine(engines: TranslateEngine[] | null, current: string | undefined): string | null {
  return current !== 'ollama' && engines?.some((e) => e.name === 'ollama' && e.key_configured) ? 'ollama' : null
}

export const DENIED_NOTE = 'Ask the PC owner to set this up.'
