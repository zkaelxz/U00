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
