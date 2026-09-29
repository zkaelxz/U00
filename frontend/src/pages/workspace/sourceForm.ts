import type { TranscribeConfigUpdate } from '../../types/workspace'

// Pure client-side checks for the Source/Transcribe stage. The server
// re-validates everything; these only save a round trip and mirror
// services/media_upload_service.py and services/transcribe_service.py.

export const UPLOAD_EXTENSIONS = ['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.mp4', '.mkv', '.mov', '.webm']

export function checkUploadFile(name: string, sizeBytes: number, maxMb: number): string | null {
  const dot = name.lastIndexOf('.')
  const ext = dot >= 0 ? name.slice(dot).toLowerCase() : ''
  if (!UPLOAD_EXTENSIONS.includes(ext)) {
    return `That file type is not supported. Use one of: ${UPLOAD_EXTENSIONS.join(' ')}.`
  }
  if (sizeBytes <= 0) return 'That file is empty.'
  if (sizeBytes > maxMb * 1024 * 1024) return `That file is larger than the ${maxMb} MB upload limit.`
  return null
}

const RANGES = {
  beam_size: { min: 1, max: 10, integer: true },
  min_silence_ms: { min: 300, max: 3000, integer: true },
  vad_threshold: { min: 0.1, max: 0.9, integer: false },
  hardsub_interval_sec: { min: 0.5, max: 3.0, integer: false },
} as const

// Returns the first out-of-range knob as a sentence, or null when valid.
export function validateConfig(update: TranscribeConfigUpdate): string | null {
  for (const [key, r] of Object.entries(RANGES)) {
    const v = update[key as keyof typeof RANGES]
    if (v === undefined) continue
    if (!Number.isFinite(v) || v < r.min || v > r.max || (r.integer && !Number.isInteger(v))) {
      return `${key.replace(/_/g, ' ')} must be ${r.integer ? 'a whole number ' : ''}between ${r.min} and ${r.max}.`
    }
  }
  return null
}

// Blank means "not set" (undefined); anything else must be a whole number in range.
export function parseExpectedSpeakers(raw: string): number | undefined | null {
  if (raw.trim() === '') return undefined
  const n = Number(raw)
  return Number.isInteger(n) && n >= 0 && n <= 20 ? n : null
}

// Mirrors core.whisper_model_warning: large-v3-turbo is weaker on ja/ko.
export function whisperModelWarning(size: string, language: string): string {
  if (size === 'large-v3-turbo' && (language === 'ja' || language === 'ko')) {
    return 'large-v3-turbo is reported noticeably weaker on Japanese and Korean -- large-v3 (or medium) is the safer choice for this drama.'
  }
  return ''
}

// Source-stage run options kept for the browser session, per drama, so a
// stage-tab switch or navigation does not wipe them.
export interface SourceFormState {
  language: string
  script: string
  transcriptText: string
  runDiarize: boolean
  speakers: string
  prompt: string
}

const formKey = (dramaId: number) => `baihe.sourceForm.${dramaId}`

export function loadSourceForm(dramaId: number): Partial<SourceFormState> {
  try {
    const raw = sessionStorage.getItem(formKey(dramaId))
    const v: unknown = raw ? JSON.parse(raw) : null
    if (!v || typeof v !== 'object') return {}
    const o = v as Record<string, unknown>
    const out: Partial<SourceFormState> = {}
    for (const k of ['language', 'script', 'transcriptText', 'speakers', 'prompt'] as const) {
      if (typeof o[k] === 'string') out[k] = o[k]
    }
    if (typeof o.runDiarize === 'boolean') out.runDiarize = o.runDiarize
    return out
  } catch {
    return {}
  }
}

export function saveSourceForm(dramaId: number, state: SourceFormState): void {
  try {
    sessionStorage.setItem(formKey(dramaId), JSON.stringify(state))
  } catch {
    // storage unavailable: the form just will not persist
  }
}

// Values the API falls back to (services/transcribe_service.py _DEFAULT_TUNING);
// the Advanced summary lists only what differs from them.
export interface AdvancedValues {
  beam_size: string
  min_silence_ms: string
  vad_threshold: string
  hardsub_interval_sec: string
  alignment_method: string
  asr_backend_choice: string
  separation_backend: string
  separate_vocals_first: boolean
  realign_long_segments: boolean
  whisper_fast_mode: boolean
  use_groq: boolean
  prompt: string
}

export function advancedSummary(v: AdvancedValues): string {
  const parts: string[] = []
  if (Number(v.beam_size) !== 5) parts.push(`beam ${v.beam_size}`)
  if (Number(v.min_silence_ms) !== 300) parts.push(`min silence ${v.min_silence_ms} ms`)
  if (Number(v.vad_threshold) !== 0.5) parts.push(`VAD ${v.vad_threshold}`)
  if (Number(v.hardsub_interval_sec) !== 1) parts.push(`hardsub every ${v.hardsub_interval_sec} s`)
  if (v.alignment_method !== 'whisper_diff') parts.push(v.alignment_method)
  if (v.asr_backend_choice !== 'whisper') parts.push(v.asr_backend_choice)
  if (v.separation_backend !== 'auto') parts.push(`separation ${v.separation_backend}`)
  if (v.separate_vocals_first) parts.push('separate vocals')
  if (v.realign_long_segments) parts.push('realign')
  if (v.whisper_fast_mode) parts.push('fast mode')
  if (v.use_groq) parts.push('Groq')
  if (v.prompt.trim()) parts.push('initial prompt')
  return parts.length ? parts.join(' · ') : 'defaults'
}

// Job ids a Source-stage run can be reattached to (services/transcribe_service.py,
// services/diarization_service.py).
export const sourceJobIds = (dramaId: number) => [`transcribe_${dramaId}`, `diarize_${dramaId}`]
