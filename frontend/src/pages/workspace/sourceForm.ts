import { safeDetail } from '../../components/errorMessages'
import type { TranscribeConfigUpdate } from '../../types/workspace'

// Pure client-side checks for the Source/Transcribe stage. The server
// re-validates everything; these only save a round trip and mirror
// services/media_upload_service.py and services/transcribe_service.py.

// tests/test_frontend_limit_parity.py keeps both lists equal to media_upload_service's.
export const AUDIO_EXTENSIONS = ['.mp3', '.wav', '.m4a', '.flac', '.ogg']
const VIDEO_EXTENSIONS = ['.mp4', '.mkv', '.mov', '.webm']
export const UPLOAD_EXTENSIONS = [...AUDIO_EXTENSIONS, ...VIDEO_EXTENSIONS]

export const isVideoFile = (name: string) => VIDEO_EXTENSIONS.some((e) => name.toLowerCase().endsWith(e))

// Audio replaces the title's media as a whole: the server unnames the old video too.
export const UPLOAD_SETS_VIDEO_ASIDE =
  "Uploading audio also sets the current video aside (kept in this title's folder), so the title will have no source video for Review or video export."
export const URL_SETS_VIDEO_ASIDE =
  "This link will give audio only, so it also sets the current video aside (kept in this title's folder). The title will have no source video for Review or video export."
export const SWITCHES_FROM_BURNED_IN =
  ' It also switches this title from reading burned-in subtitles to transcribing the audio.'

// A direct link to an audio file is installed as audio only even with Audio only off
// (services/url_media_service.py); the server reads the extension of the URL path.
export function isDirectAudioUrl(url: string): boolean {
  let path: string
  try {
    path = new URL(url.trim()).pathname
  } catch {
    return false
  }
  const name = path.slice(path.lastIndexOf('/') + 1)
  const dot = name.lastIndexOf('.')
  return dot > 0 && AUDIO_EXTENSIONS.includes(name.slice(dot).toLowerCase())
}

// The link target for "Change it in Settings"; the server's error text names the same place.
export const UPLOAD_LIMIT_SETTINGS_HREF = '#/settings?section=uploads'

export function isUploadLimitProblem(message: string): boolean {
  return message.includes('upload limit')
}

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

// Keep in sync with MIN_SILENCE_MS_MIN/MAX in core.py.
export const MIN_SILENCE_MS_MIN = 100
export const MIN_SILENCE_MS_MAX = 3000

// Keep in sync with MIN_WORD_GAP_SECONDS (default) and its _MIN/_MAX in core.py.
export const MIN_PAUSE_SEC_DEFAULT = 0.35
export const MIN_PAUSE_SEC_MIN = 0.1
export const MIN_PAUSE_SEC_MAX = 2.0

// Labels match the Transcribe stage's fields.
const RANGES = {
  beam_size: { label: 'Beam size', min: 1, max: 10, integer: true },
  min_silence_ms: { label: 'Min silence (ms)', min: MIN_SILENCE_MS_MIN, max: MIN_SILENCE_MS_MAX, integer: true },
  min_pause_sec: { label: 'Pause that can split a long line (s)', min: MIN_PAUSE_SEC_MIN, max: MIN_PAUSE_SEC_MAX, integer: false },
  vad_threshold: { label: 'VAD threshold', min: 0.1, max: 0.9, integer: false },
  hardsub_interval_sec: { label: 'Hardsub interval (s)', min: 0.5, max: 3.0, integer: false },
} as const

const DEFAULT_HALLUCINATION_SILENCE_SEC = 2

// Returns the first out-of-range knob as a sentence, or null when valid.
export function validateConfig(update: TranscribeConfigUpdate): string | null {
  for (const [key, r] of Object.entries(RANGES)) {
    const v = update[key as keyof typeof RANGES]
    if (v === undefined) continue
    if (!Number.isFinite(v) || v < r.min || v > r.max || (r.integer && !Number.isInteger(v))) {
      return `${r.label} must be ${r.integer ? 'a whole number ' : ''}between ${r.min} and ${r.max}.`
    }
  }
  const h = update.hallucination_silence_sec
  if (h !== undefined && (!Number.isFinite(h) || (h !== 0 && (h < 0.5 || h > 10)))) {
    return 'Hallucination guard must be 0 (off) or between 0.5 and 10 seconds.'
  }
  return null
}

// Blank means "not set" (undefined); anything else must be a whole number in range.
export function parseExpectedSpeakers(raw: string): number | undefined | null {
  if (raw.trim() === '') return undefined
  const n = Number(raw)
  return Number.isInteger(n) && n >= 0 && n <= 20 ? n : null
}

interface ParsedSpeakerHints {
  expected?: number
  min?: number
  max?: number
}

// Exact count (0-20, blank or 0 = auto) or a min/max range (each 1-20,
// either may be blank), mirroring diarize.validate_speaker_hints.
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

// A run option the server refuses, tied to the field to highlight.
export type RunField = 'alignment_method' | 'asr_backend_choice' | 'speakers'
export interface RunFieldProblem {
  field: RunField
  message: string
}

// Mirrors transcribe_service.validate_transcribe_options: option pairs the
// server refuses for this drama's mode, caught before a round trip.
export function runOptionProblem(
  mode: string | undefined,
  alignment: string,
  asr: string,
  mossEnabled: boolean,
): RunFieldProblem | null {
  if (mode === 'whisper' && alignment === 'qwen3_forced_align') {
    return {
      field: 'alignment_method',
      message: 'Qwen3 forced alignment needs a transcript to align, but this drama transcribes with Whisper alone. Pick Whisper (diff) or supply a transcript.',
    }
  }
  if (mode === 'whisper' && asr === 'moss_td' && !mossEnabled) {
    return {
      field: 'asr_backend_choice',
      message: 'MOSS-Transcribe-Diarize is experimental and turned off. Turn it on in Settings, or pick another ASR backend.',
    }
  }
  return null
}

// A 422 whose own sentence names an option: the field to highlight, with that
// sentence (only fixed server sentences; anything path- or key-like is dropped).
export function runProblemFromError(err: unknown): RunFieldProblem | null {
  const e = err as { code?: string; message?: string } | null
  if (!e || (e.code !== 'validation_error' && e.code !== 'invalid_input') || !e.message) return null
  const message = safeDetail(e.message)
  if (!message) return null
  if (/forced alignment/i.test(message)) return { field: 'alignment_method', message }
  if (/\bMOSS\b/.test(message)) return { field: 'asr_backend_choice', message }
  if (/speakers?\b/i.test(message)) return { field: 'speakers', message }
  return null
}

// What our benchmarks showed for turbo vs large-v3 (docs/asr-experiments.md):
// Korean slightly favoured large-v3, Chinese was mixed, Japanese was a tie.
export function whisperModelWarning(size: string, language: string): string {
  if (size !== 'large-v3-turbo') return ''
  if (language === 'ko') {
    return 'On Korean speech in our tests, large-v3 made about half a point fewer character errors than turbo, and was about twice as slow.'
  }
  if (language === 'zh') {
    return 'On Chinese our tests disagree: large-v3 was more accurate on clean speech, turbo on one drama clip.'
  }
  return ''
}

// Source-stage run options kept for the browser session, per drama, so a
// stage-tab switch or navigation does not wipe them.
interface SourceFormState {
  language: string
  script: string
  transcriptText: string
  runDiarize: boolean
  speakers: string
  // Speaker-count range for "Detect speakers only".
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
  min_pause_sec: string
  vad_threshold: string
  // Absent from older callers: reads as normal.
  sensitivity_preset?: string
  hallucination_silence_sec: string
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
  if (Number(v.min_pause_sec) !== MIN_PAUSE_SEC_DEFAULT) parts.push(`split pause ${v.min_pause_sec} s`)
  if (Number(v.vad_threshold) !== 0.5) parts.push(`VAD ${v.vad_threshold}`)
  if (v.sensitivity_preset === 'sensitive') parts.push('more sensitive')
  if (Number(v.hallucination_silence_sec) !== DEFAULT_HALLUCINATION_SILENCE_SEC) {
    parts.push(Number(v.hallucination_silence_sec) === 0 ? 'no hallucination guard' : `hallucination guard ${v.hallucination_silence_sec} s`)
  }
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
// services/diarization_service.py, services/url_media_service.py).
export const sourceJobIds = (dramaId: number) => [
  `transcribe_${dramaId}`,
  `diarize_${dramaId}`,
  `ocrchapter_${dramaId}`,
  `extract_audio_${dramaId}`,
  `urlmedia_${dramaId}`,
]

// Chapter OCR (services/novel_attach_service.py _BACKENDS, MAX_IMAGES, _IMAGE_EXTENSIONS).
const OCR_BACKENDS: Record<string, string[]> = {
  zh: ['tesseract', 'paddle'],
  ja: ['manga_ocr', 'tesseract'],
  ko: ['tesseract'],
}
export const ocrBackendOptions = (language: string | null): string[] => OCR_BACKENDS[language ?? 'zh'] ?? ['tesseract']

const OCR_MAX_IMAGES = 200
const OCR_EXTENSIONS = ['.png', '.jpg', '.jpeg']

export function checkOcrImages(names: string[]): string | null {
  if (names.length === 0) return null
  if (names.length > OCR_MAX_IMAGES) return `Choose at most ${OCR_MAX_IMAGES} images.`
  const bad = names.find((n) => !OCR_EXTENSIONS.includes(n.slice(n.lastIndexOf('.')).toLowerCase()))
  return bad ? `${bad}: only PNG or JPG images are supported.` : null
}
