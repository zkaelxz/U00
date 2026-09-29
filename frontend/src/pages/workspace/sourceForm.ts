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

export interface ParsedSpeakerHints {
  expected?: number
  min?: number
  max?: number
}

// Exact count (0-20, blank or 0 = auto) or a min/max range (each 1-20,
// either may be blank), mirroring diarize.validate_speaker_hints (Step 105).
// Returns the parsed hints, or a plain-English problem string.
export function parseSpeakerHints(expectedRaw: string, minRaw: string, maxRaw: string): ParsedSpeakerHints | string {
  const expected = parseExpectedSpeakers(expectedRaw)
  if (expected === null) return 'Expected speakers must be a whole number from 0 to 20.'
  const bound = (raw: string): number | undefined | null => {
    if (raw.trim() === '') return undefined
    const n = Number(raw)
    return Number.isInteger(n) && n >= 1 && n <= 20 ? n : null
  }
  const min = bound(minRaw)
  const max = bound(maxRaw)
  if (min === null || max === null) return 'Min and max speakers must be whole numbers from 1 to 20.'
  if (min !== undefined && max !== undefined && min > max) return "Min speakers can't be more than max speakers."
  if (expected && (min !== undefined || max !== undefined)) {
    return 'Use either an exact speaker count or a min/max range, not both.'
  }
  return { expected, min, max }
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
  // Speaker-count range for "Detect speakers only" (Step 105).
  minSpeakers?: string
  maxSpeakers?: string
  // Extra names added to the automatic Whisper prompt.
  extraNames: string
}

const formKey = (dramaId: number) => `baihe.sourceForm.${dramaId}`

export function loadSourceForm(dramaId: number): Partial<SourceFormState> {
  try {
    const raw = sessionStorage.getItem(formKey(dramaId))
    const v: unknown = raw ? JSON.parse(raw) : null
    if (!v || typeof v !== 'object') return {}
    const o = v as Record<string, unknown>
    const out: Partial<SourceFormState> = {}
    for (const k of ['language', 'script', 'transcriptText', 'speakers', 'minSpeakers', 'maxSpeakers', 'extraNames'] as const) {
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
  if (v.prompt.trim()) parts.push('replacement prompt')
  return parts.length ? parts.join(' · ') : 'defaults'
}

// Job ids a Source-stage run can be reattached to (services/transcribe_service.py,
// services/diarization_service.py).
export const sourceJobIds = (dramaId: number) => [
  `transcribe_${dramaId}`,
  `diarize_${dramaId}`,
  `ocrchapter_${dramaId}`,
  `extract_audio_${dramaId}`,
]

// Chapter OCR (services/novel_attach_service.py _BACKENDS, MAX_IMAGES, _IMAGE_EXTENSIONS).
const OCR_BACKENDS: Record<string, string[]> = {
  zh: ['tesseract', 'paddle'],
  ja: ['manga_ocr', 'tesseract'],
  ko: ['tesseract'],
}
export const ocrBackendOptions = (language: string | null): string[] => OCR_BACKENDS[language ?? 'zh'] ?? ['tesseract']

export const OCR_MAX_IMAGES = 200
const OCR_EXTENSIONS = ['.png', '.jpg', '.jpeg']

export function checkOcrImages(names: string[]): string | null {
  if (names.length === 0) return null
  if (names.length > OCR_MAX_IMAGES) return `Choose at most ${OCR_MAX_IMAGES} images.`
  const bad = names.find((n) => !OCR_EXTENSIONS.includes(n.slice(n.lastIndexOf('.')).toLowerCase()))
  return bad ? `${bad}: only PNG or JPG images are supported.` : null
}
