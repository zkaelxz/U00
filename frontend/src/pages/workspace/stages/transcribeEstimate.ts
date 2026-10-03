// Rough, conservative run-time captions for the Transcribe stage. The
// figures below are typical ranges, not measurements: real times depend on
// the PC. They scale off the media's duration by Whisper model size and GPU
// on/off, and a speed measured on this PC's last run replaces them.

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

// Fast mode (batched decoding) is typically 2-4x quicker; halving is the cautious end.
const FAST_MODE_FACTOR = 0.5

export interface WhisperEstimateInput {
  audioSeconds: number | null // null or 0 = unknown
  whisperSize: string
  useGpu: boolean | null // null = unknown, estimated as CPU (the slower case)
  fastMode?: boolean
  measuredSpeed?: number | null // seconds of audio per second of work, from the last run here
}

// Seconds of work as [low, high], or null when it can't be estimated (no length, or a model with no table entry).
export function whisperEstimateSeconds(i: WhisperEstimateInput): { low_s: number; high_s: number } | null {
  if (!i.audioSeconds || i.audioSeconds <= 0) return null
  const speed = i.measuredSpeed
  if (typeof speed === 'number' && Number.isFinite(speed) && speed > 0) {
    // One measurement is not exact either: leave room both ways.
    const t = i.audioSeconds / speed
    return { low_s: t * 0.8, high_s: t * 1.3 }
  }
  const factor = (i.useGpu ? WHISPER_GPU : WHISPER_CPU)[i.whisperSize]
  if (!factor) return null
  const k = i.fastMode ? FAST_MODE_FACTOR : 1
  return { low_s: i.audioSeconds * factor[0] * k, high_s: i.audioSeconds * factor[1] * k }
}

// "under a minute", "about 40-70 min", "about 2-3 h".
export function roughRange(lowSeconds: number, highSeconds: number): string {
  if (highSeconds < 60) return 'under a minute'
  const mins = (s: number) => {
    const m = s / 60
    return m >= 10 ? Math.round(m / 5) * 5 : Math.max(1, Math.round(m))
  }
  if (highSeconds < 5400) {
    const a = mins(lowSeconds)
    const b = mins(highSeconds)
    return a === b ? `about ${a} min` : `about ${a}-${b} min`
  }
  const hrs = (s: number) => Math.round(s / 1800) / 2
  const a = hrs(lowSeconds)
  const b = hrs(highSeconds)
  return a === b ? `about ${a} h` : `about ${a}-${b} h`
}

interface TranscribeEstimateInput extends WhisperEstimateInput {
  useGroq: boolean
  detectSpeakers: boolean
  modelCached?: boolean
}

// The caption next to Transcribe, or null when it can't be estimated here.
export function transcribeEstimate(i: TranscribeEstimateInput): string | null {
  if (i.useGroq) return null // a cloud run: its time isn't this PC's
  const est = whisperEstimateSeconds(i)
  if (!est || !i.audioSeconds) return null
  const measured = typeof i.measuredSpeed === 'number' && i.measuredSpeed > 0
  const where = i.useGpu ? 'GPU' : 'CPU'
  const speakers = i.detectSpeakers ? `, plus ${range(i.audioSeconds, DIARIZE)} to detect speakers` : ''
  const basis = measured ? ', based on your last run' : ''
  const download = i.modelCached === false ? ' First use also downloads the model.' : ''
  return `Rough estimate: ${roughRange(est.low_s, est.high_s)} for this audio (${i.whisperSize} on ${where}${basis})${speakers}.${download}`
}

// The caption next to "Detect speakers only".
export function diarizeEstimate(durationSeconds: number | null): string {
  if (!durationSeconds || durationSeconds <= 0) return 'Takes approx. a minute to a few minutes, depending on audio length.'
  return `Takes approx. ${range(durationSeconds, DIARIZE)}.`
}

// --- Live ETA for a running transcription ---------------------------------

export interface EtaSample {
  t: number // seconds, any fixed origin
  p: number // progress 0..1
}

// Wait this long after the first percent before saying anything: earlier
// readings swing too much to be worth showing.
export const ETA_MIN_SECONDS = 30
const ETA_MIN_PROGRESS = 0.01
const ETA_MAX_SPREAD = 2

// Seconds left, or null when it shouldn't be shown. `samples` run from the
// first reported percent (p > 0) of one stage; each is extrapolated on its own
// (time since the first percent, over the progress made since then) and the
// median of the recent ones is shown, so one slow poll doesn't move it. Hidden
// when those readings disagree by more than 2x or progress has stopped.
export function liveEtaSeconds(samples: EtaSample[], now: number): number | null {
  if (samples.length < 2) return null
  const first = samples[0]
  const last = samples[samples.length - 1]
  if (first.p <= 0 || now - first.t < ETA_MIN_SECONDS) return null
  if (last.p - first.p < ETA_MIN_PROGRESS || last.p >= 1) return null
  const sinceChange = samples.find((s) => s.p === last.p)!.t
  const stalledFor = now - sinceChange
  if (stalledFor > Math.max(60, (now - first.t) * 0.25)) return null
  const left: number[] = []
  for (const s of samples) {
    if (s.t - first.t < 10 || s.p - first.p < ETA_MIN_PROGRESS) continue
    left.push(((s.t - first.t) * (1 - s.p)) / (s.p - first.p) - (now - s.t))
  }
  const recent = left.slice(-5)
  if (recent.length < 2) return null
  const positive = recent.map((v) => Math.max(v, 60))
  if (Math.max(...positive) / Math.min(...positive) > ETA_MAX_SPREAD) return null
  const sorted = [...positive].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

// "about 12 min left".
export function formatLeft(seconds: number): string {
  return seconds < 90 ? 'about 1 min left' : `${roughDuration(seconds)} left`
}

// The job says when a stage has no percent of its own (model load, separation
// model, Qwen3-ASR before its first batch): no ETA is extrapolated there.
export function isNoPercentStage(message: string): boolean {
  return /no progress is available|no percent/i.test(message)
}

// Qwen3-ASR runs report "(step 1 of 2)" / "(step 2 of 2)": each step has its
// own clock, so samples never mix across them.
export function etaStage(message: string): string {
  const m = /step (\d) of \d/i.exec(message)
  return m ? m[1] : '0'
}
