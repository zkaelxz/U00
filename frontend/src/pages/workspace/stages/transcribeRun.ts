// What a Transcribe run will actually use, from the form: the one line above the button, the
// controls the chosen ASR backend ignores, and the advisor note. Pure so a vitest pins the wording.

// Backends that skip Whisper entirely (services/transcribe_pipeline.VAD_BACKENDS). Plain qwen3_asr
// is not here: Whisper still cuts its lines and Qwen3 rewrites the text.
const WHISPER_FREE_BACKENDS = ['qwen3_asr_vad', 'qwen3_asr_long', 'anime_whisper']

const QWEN3_ASR_MODEL = 'Qwen3-ASR 1.7B'

const BACKEND_NAMES: Record<string, string> = {
  whisper: 'Whisper',
  qwen3_asr: 'Qwen3 ASR',
  qwen3_asr_vad: 'Qwen3 ASR with speech detection',
  qwen3_asr_long: 'Qwen3 ASR on long windows',
  anime_whisper: 'Anime-whisper',
}

export const backendRunsWhisper = (backend: string): boolean => !WHISPER_FREE_BACKENDS.includes(backend)

/** Why the Whisper-only controls are off, or null when the run uses Whisper. */
export function whisperNotUsedReason(backend: string): string | null {
  if (backendRunsWhisper(backend)) return null
  return backend === 'anime_whisper' ? 'Not used with Anime-whisper' : 'Not used with Qwen3 ASR'
}

export type WhisperOnlyControl = 'model' | 'fast_mode' | 'repeat_guard' | 'hallucination_guard' | 'vad_threshold' | 'min_silence' | 'beam_size'

/** Why a Whisper-only control is off for this backend, or null when the run uses it. Anime-whisper takes the
 *  repeat guard (its decode penalties); no other Whisper control reaches either Whisper-free backend. */
export function whisperOnlyControlReason(backend: string, control: WhisperOnlyControl): string | null {
  if (control === 'repeat_guard' && backend === 'anime_whisper') return null
  return whisperNotUsedReason(backend)
}

/** The model that will run when it is not the Whisper size on the form. */
export function replacementModel(backend: string): string | null {
  if (backendRunsWhisper(backend)) return null
  return backend === 'anime_whisper' ? 'anime-whisper' : QWEN3_ASR_MODEL
}

interface RunLineInput {
  backend: string
  whisperSize: string
  useGroq: boolean
  /** The Use GPU setting; null while it has not loaded. */
  useGpu: boolean | null
  /** gpu_runtime.status from the config: 'missing' means CUDA's cuBLAS cannot load, so faster-whisper runs on the CPU. */
  gpuRuntime?: string
  /** Whether an NVIDIA GPU was found on this PC; undefined or null while readiness has not loaded. */
  gpuFound?: boolean | null
  /** A supplied transcript is aligned instead of transcribed. */
  haveTranscript?: boolean
  alignmentMethod?: string
}

function gpuPart(i: RunLineInput, ctranslate2: boolean): string {
  if (i.useGpu === null) return 'GPU unknown'
  if (!i.useGpu) return 'GPU off'
  // The setting says on, but ctranslate2 (Whisper) falls back to the CPU without cuBLAS. Qwen3 runs on torch's own CUDA.
  if (ctranslate2 && i.gpuRuntime === 'missing') return 'GPU off (CUDA libraries missing, runs on CPU)'
  return i.gpuFound === false ? 'GPU on, none found' : 'GPU on'
}

/** "This run will use: <backend> · <model> · GPU on/off". */
export function runLine(i: RunLineInput): string {
  let backend: string
  let model: string
  let ctranslate2 = true
  if (i.haveTranscript) {
    if (i.alignmentMethod === 'qwen3_forced_align') {
      backend = 'Qwen3 forced alignment'
      model = 'Qwen3 forced aligner'
      ctranslate2 = false
    } else {
      backend = 'Whisper alignment'
      model = `Whisper ${i.whisperSize}`
    }
  } else if (i.useGroq && i.backend === 'whisper') {
    return 'This run will use: Groq cloud · whisper-large-v3-turbo · GPU not used (runs on Groq)'
  } else if (i.backend === 'qwen3_asr') {
    backend = BACKEND_NAMES.qwen3_asr
    model = `${i.useGroq ? 'Groq whisper-large-v3-turbo' : `Whisper ${i.whisperSize}`} cuts lines, ${QWEN3_ASR_MODEL} writes the text`
  } else if (!backendRunsWhisper(i.backend)) {
    backend = BACKEND_NAMES[i.backend] ?? i.backend
    model = replacementModel(i.backend) ?? i.backend
    ctranslate2 = false
  } else {
    backend = BACKEND_NAMES.whisper
    model = `Whisper ${i.whisperSize}`
  }
  return `This run will use: ${backend} · ${model} · ${gpuPart(i, ctranslate2)}`
}

interface RunSettingsInput {
  language: string
  /** The script the form sets, or '' for the title's own. */
  script: string
  titleScript?: string
  voiceDetection: string
  beamSize: number
  separateVocals: boolean
  useGroq: boolean
  runDiarize: boolean
  speakers: string
  extraNames: string
  promptOverride: string
}

const LANGUAGE_WORDS: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean' }

/** The options that decide how long and how good the run is, in one line the person can check before a long run. */
export function runSettingsLine(i: RunSettingsInput): string {
  const language = LANGUAGE_WORDS[i.language] ?? i.language
  const script = i.language === 'zh' ? ` (${(i.script || i.titleScript || 'simplified')} script)` : ''
  const parts = [
    `${language}${script}`,
    `voice detection ${i.voiceDetection}`,
    `beam ${i.beamSize}`,
    `vocals first ${i.separateVocals ? 'on' : 'off'}`,
    i.runDiarize ? `speakers on${i.speakers.trim() && i.speakers.trim() !== '0' ? `, ${i.speakers.trim()}` : ''}` : 'speakers off',
  ]
  if (i.promptOverride.trim()) parts.push('your own prompt')
  else if (i.extraNames.trim()) parts.push('extra names added')
  if (i.useGroq) parts.push('Groq cloud (paid)')
  return parts.join(' · ')
}
