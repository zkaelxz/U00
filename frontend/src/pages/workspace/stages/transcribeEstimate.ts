// Parity D04: rough, conservative run-time captions for the Transcribe stage.
// No job reports an ETA, so these scale off the media's duration: Whisper by
// model size and GPU on/off, speaker detection 1x-2x the audio length (the
// same range as services/diarization_service.diarization_estimate_caption).
// Always labelled "approx."; real times depend on the PC.

// Processing time as a fraction of the audio length: [fast, slow].
const WHISPER_CPU: Record<string, [number, number]> = {
  tiny: [0.1, 0.3],
  base: [0.15, 0.5],
  small: [0.3, 1],
  medium: [0.8, 2.5],
  'large-v3': [1.5, 4],
  'large-v3-turbo': [0.6, 2],
}
const WHISPER_GPU: Record<string, [number, number]> = {
  tiny: [0.02, 0.1],
  base: [0.03, 0.1],
  small: [0.05, 0.2],
  medium: [0.1, 0.4],
  'large-v3': [0.15, 0.6],
  'large-v3-turbo': [0.08, 0.3],
}
const SLOWEST: [number, number] = [1.5, 4]
const DIARIZE: [number, number] = [1, 2]

// Seconds -> "under a minute", "about 4 min", "about 1 h 20 min".
export function roughDuration(seconds: number): string {
  if (seconds < 60) return 'under a minute'
  const mins = Math.round(seconds / 60)
  if (mins < 60) return `about ${mins} min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return `about ${h} h${m ? ` ${m} min` : ''}`
}

const range = (seconds: number, [lo, hi]: [number, number]) => {
  const a = roughDuration(seconds * lo)
  const b = roughDuration(seconds * hi)
  return a === b ? a : `${a.replace(/^about /, '')} to ${b.replace(/^about /, '')}`
}

interface TranscribeEstimateInput {
  durationSeconds: number | null // null or 0 = unknown
  whisperSize: string
  useGpu: boolean | null // null = unknown, estimated as CPU (the slower case)
  useGroq: boolean
  detectSpeakers: boolean
}

// The caption next to Transcribe, or null when it can't be estimated here.
export function transcribeEstimate(i: TranscribeEstimateInput): string | null {
  if (!i.durationSeconds || i.durationSeconds <= 0) return null
  if (i.useGroq) return null // a cloud run: its time isn't this PC's
  const table = i.useGpu ? WHISPER_GPU : WHISPER_CPU
  const factor = table[i.whisperSize] ?? SLOWEST
  const where = i.useGpu ? 'on the GPU' : 'on the CPU'
  const speakers = i.detectSpeakers ? `, plus ${range(i.durationSeconds, DIARIZE)} to detect speakers` : ''
  return `Takes approx. ${range(i.durationSeconds, factor)} with ${i.whisperSize} ${where}${speakers}.`
}

// The caption next to "Detect speakers only".
export function diarizeEstimate(durationSeconds: number | null): string {
  if (!durationSeconds || durationSeconds <= 0) return 'Takes approx. a minute to a few minutes, depending on audio length.'
  return `Takes approx. ${range(durationSeconds, DIARIZE)}.`
}
