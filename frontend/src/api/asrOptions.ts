// Experimental transcription settings (api/routers/asr_options_routes.py,
// Steps 103/104) and the Diarize-stage config read (Step 101's device note).
// Types mirror api/asr_options_schemas.py and api/schemas/transcribe.py's DiarizationConfig.
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export interface AsrOptions {
  qwen_asr_batch_size: number
  qwen_asr_batch_min: number
  qwen_asr_batch_max: number
  // Installed qwen-asr version (null if not installed); batching only runs
  // with the tested version, any other sends one line at a time.
  qwen_asr_version: string | null
  qwen_asr_batching_available: boolean
  moss_experimental: boolean
  // Qwen3 ASR with speech detection: also refine line times with the forced aligner.
  qwen_vad_refine_timing: boolean
  // Detect the spoken language of each speech span and mark lines that differ from the title's.
  mixed_languages: boolean
  moss_installed: boolean
}

export interface AsrOptionsUpdate {
  qwen_asr_batch_size?: number
  moss_experimental?: boolean
  qwen_vad_refine_timing?: boolean
  mixed_languages?: boolean
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

// Transcribe > Advanced "ASR backend" choices: MOSS (Step 104) only while its
// experimental toggle is on. A drama already set to it still shows it (the
// select keeps the current value).
export function asrBackendOptions(mossEnabled: boolean): string[] {
  return mossEnabled
    ? ['whisper', 'qwen3_asr', 'qwen3_asr_vad', 'moss_td']
    : ['whisper', 'qwen3_asr', 'qwen3_asr_vad']
}

// The muted line under the batch-size field (Step 103).
export function batchingNote(o: Pick<AsrOptions, 'qwen_asr_version' | 'qwen_asr_batching_available'>): string {
  if (o.qwen_asr_batching_available) return `Batching can run with the installed qwen-asr ${o.qwen_asr_version}.`
  if (!o.qwen_asr_version) return 'qwen-asr is not installed, so nothing is batched.'
  return `Batching is tested with qwen-asr 0.0.6 only; with ${o.qwen_asr_version} installed, lines are sent one at a time.`
}
