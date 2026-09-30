// Experimental transcription settings (api/routers/asr_options_routes.py,
// Steps 103/104) and the Diarize-stage config read (Step 101's device note).
// Types mirror api/asr_options_schemas.py and api/schemas.py's DiarizationConfig.
import { getJson, postJson } from './client'
import { pcOnlyFetch } from './pcOnly'

type Fetch = typeof fetch

export interface AsrOptions {
  qwen_asr_batch_size: number
  qwen_asr_batch_min: number
  qwen_asr_batch_max: number
  moss_experimental: boolean
  moss_installed: boolean
}

export interface AsrOptionsUpdate {
  qwen_asr_batch_size?: number
  moss_experimental?: boolean
}

export interface DiarizationConfig {
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
  if (device === 'cuda') return 'Last speaker detection ran on the GPU.'
  if (device === 'cpu') return 'Last speaker detection ran on the CPU (GPU off in Settings, or not available).'
  return `Last speaker detection ran on: ${device}.`
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
  return mossEnabled ? ['whisper', 'qwen3_asr', 'moss_td'] : ['whisper', 'qwen3_asr']
}
