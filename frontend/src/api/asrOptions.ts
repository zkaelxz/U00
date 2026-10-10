// Experimental transcription settings (api/routers/asr_options_routes.py)
// and the Diarize-stage config read (its device note).
// Types mirror api/asr_options_schemas.py and api/schemas/transcribe.py's DiarizationConfig.
import { getJson, postJson } from './client'
import type { JobRecord } from '../types/jobs'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export interface AsrOptions {
  qwen_asr_batch_size: number
  qwen_asr_batch_min: number
  qwen_asr_batch_max: number
  // Installed transformers version (null if not installed); Qwen3-ASR, and so
  // batching, needs 5.15 or newer. The field names predate the move off the qwen-asr package.
  qwen_asr_version: string | null
  qwen_asr_batching_available: boolean
  // Qwen3 ASR with speech detection: also refine line times with the forced aligner.
  qwen_vad_refine_timing: boolean
  // Detect the spoken language of each speech span and mark lines that differ from the title's.
  mixed_languages: boolean
  voice_detector: VoiceDetector
  // Booleans only: the model's folder and download address stay on the PC.
  asmr_vad_onnxruntime_installed: boolean
  asmr_vad_model_downloaded: boolean
  asmr_vad_download_job_id: string
}

export type VoiceDetector = 'auto' | 'asmr' | 'standard'

export interface AsrOptionsUpdate {
  qwen_asr_batch_size?: number
  qwen_vad_refine_timing?: boolean
  mixed_languages?: boolean
  voice_detector?: VoiceDetector
}

interface DiarizationConfig {
  drama_id: number
  hf_token_configured: boolean
  expected_speakers: number | null
  min_speakers: number | null
  max_speakers: number | null
  // 'cuda' or 'cpu': where the last speaker detection actually ran.
  last_device: string | null
  audio_available: boolean
}

const BASE = '/api/settings/asr-options'

export const getAsrOptions = (f?: Fetch) => getJson<AsrOptions>(BASE, f)

// PC only: a 403 marks the tab remote.
export const updateAsrOptions = (update: AsrOptionsUpdate, f?: Fetch) =>
  postJson<AsrOptions>(BASE, update, pcOnlyFetch(f))

export const getDiarizationConfig = (id: number, f?: Fetch) =>
  getJson<DiarizationConfig>(`/api/diarization/dramas/${id}/config`, f)

// Plain words for DiarizationConfig.last_device, or null before any run recorded one.
export function deviceNote(device: string | null | undefined): string | null {
  if (!device) return null
  if (device === 'cuda') return 'Last Detect speakers run (pyannote) used the GPU.'
  if (device === 'cpu') return 'Last Detect speakers run (pyannote) used the CPU (GPU off in Settings, or not available).'
  return `Last Detect speakers run (pyannote) used: ${device}.`
}

// '' or a whole number within [min, max] -> the number; anything else -> null.
export function parseBatchSize(raw: string, min: number, max: number): number | null {
  const t = raw.trim()
  if (!/^\d+$/.test(t)) return null
  const n = Number(t)
  return n >= min && n <= max ? n : null
}

// Transcribe > Advanced "ASR backend" choices.
export function asrBackendOptions(): string[] {
  return ['whisper', 'qwen3_asr', 'qwen3_asr_vad', 'qwen3_asr_long']
}

// The muted line under the batch-size field.
export function batchingNote(o: Pick<AsrOptions, 'qwen_asr_version' | 'qwen_asr_batching_available'>): string {
  if (o.qwen_asr_batching_available) return `Batching can run with the installed transformers ${o.qwen_asr_version}.`
  if (!o.qwen_asr_version) return 'transformers is not installed, so Qwen3 ASR cannot run.'
  return `Qwen3 ASR needs transformers 5.15 or newer; ${o.qwen_asr_version} is installed. Update it in Diagnostics.`
}

export const VOICE_DETECTOR_LABELS: Record<VoiceDetector, string> = {
  auto: 'Auto (ASMR for Japanese ASMR titles)',
  asmr: 'ASMR',
  standard: 'Standard',
}

// PC only: starts the opt-in model download (about 119 MB).
export const startVoiceDetectorDownload = (f?: Fetch) =>
  postJson<{ job_id: string; started: boolean }>(`${BASE}/voice-detector/download`, {}, pcOnlyFetch(f))

// A finished download job that did not leave the model behind, in words; null while it runs or when it worked.
export function downloadProblem(job: Pick<JobRecord, 'status' | 'error' | 'outcome_message' | 'result'>): string | null {
  if (job.status === 'cancelled') return 'The ASMR detector download was cancelled.'
  if (job.status !== 'error') return null
  const detail = job.result && typeof job.result.detail === 'string' ? job.result.detail : null
  return detail ?? job.error ?? job.outcome_message ?? 'The ASMR detector download failed.'
}

// The muted line under the choice: what is missing for the ASMR detector, or null when nothing is.
// Auto and ASMR both fall back to Standard on a run, so a missing piece is a heads-up, not an error.
export function voiceDetectorNote(
  o: Pick<AsrOptions, 'voice_detector' | 'asmr_vad_onnxruntime_installed' | 'asmr_vad_model_downloaded'>,
): string | null {
  if (o.voice_detector === 'standard') return null
  if (!o.asmr_vad_onnxruntime_installed) {
    return 'The ASMR detector needs onnxruntime (install it in Diagnostics > Packages). Until then, runs use Standard.'
  }
  if (!o.asmr_vad_model_downloaded) {
    return 'The ASMR detector model is not downloaded yet, so runs use Standard until it is.'
  }
  return null
}
