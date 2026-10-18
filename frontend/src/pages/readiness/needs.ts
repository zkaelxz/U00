// What a control needs from this PC, as plain data. Pure of React: a vitest pins the wording and the
// readiness-to-disabled mapping per control. Anything unknown (readiness not loaded) counts as
// ready, so a control is never switched off on a guess; the server still refuses what cannot run.
import type { SetupReadiness } from '../../types/setup'

/** What the person can do about it. Only Whisper and GPU PyTorch install in place; the rest open Diagnostics or Settings. */
export type NeedAction =
  | 'install_whisper'
  | 'install_gpu_torch'
  | 'recheck'
  | 'diagnostics'
  | 'settings'
  // Nothing to install from here (a separate program): the link beside it is the way.
  | 'external'

export interface Need {
  id: string
  /** Short name for the list: "Vocal separation". */
  label: string
  /** What is missing and why it matters, as one sentence. */
  reason: string
  action: NeedAction
  /** Diagnostics package keys still missing; the first is preselected on the Packages card. */
  packages: string[]
  size?: string
  /** A suggestion, not a requirement: shown calmly and never blocks a run. */
  recommended?: boolean
  /** An extra link beside the action (the Hugging Face terms page). */
  link?: { href: string; label: string }
}

export const GPU_TORCH_SIZE = 'about 2.5 GB'
// Mirrored from qwen3_native.APPROX_DOWNLOAD_GB (the 1.7B model; the aligner is 1.8 GB).
export const QWEN3_MODEL_SIZE = 'about 4.1 GB'
const QWEN3_ALIGNER_SIZE = 'about 1.8 GB'
// The model a speaker-detection run needs access to (diarize.DIARIZATION_MODELS[0]).
export const SPEAKER_MODEL_URL = 'https://huggingface.co/pyannote/speaker-diarization-community-1'

export const QWEN3_BACKENDS = ['qwen3_asr', 'qwen3_asr_vad', 'qwen3_asr_long']
// Backends that skip Whisper entirely (the same list as transcribeRun.ts's WHISPER_FREE_BACKENDS).
const WHISPER_FREE = ['qwen3_asr_vad', 'qwen3_asr_long', 'anime_whisper']

const names = (list: string[]) => (list.length <= 1 ? list.join('') : `${list.slice(0, -1).join(', ')} and ${list[list.length - 1]}`)

export function whisperNeed(r: SetupReadiness | null): Need | null {
  if (!r || r.faster_whisper) return null
  return {
    id: 'whisper', label: 'Transcription (Whisper)', action: 'install_whisper', packages: ['faster_whisper'],
    reason: "Transcription (Whisper) isn't installed. Baihe needs it to turn speech into lines.",
  }
}

export function ffmpegNeed(r: SetupReadiness | null): Need | null {
  if (!r || r.ffmpeg) return null
  return {
    id: 'ffmpeg', label: 'FFmpeg', action: 'recheck', packages: [],
    reason: "FFmpeg isn't installed or isn't on the PATH. Baihe needs it to read audio and video.",
  }
}

export function separationNeed(r: SetupReadiness | null, backend: string): Need | null {
  if (!r) return null
  const separator = backend === 'demucs' ? r.demucs : backend === 'audio_separator' ? r.audio_separator : r.audio_separator || r.demucs
  if (separator) return null
  const missing = backend === 'demucs' ? ['demucs'] : backend === 'audio_separator' ? ['audio-separator'] : ['audio-separator']
  const what = backend === 'demucs' ? 'Demucs' : backend === 'audio_separator' ? 'audio-separator' : 'audio-separator (or Demucs)'
  return {
    id: 'separation', label: 'Vocal separation', action: 'diagnostics', packages: missing,
    reason: `Separating vocals first needs ${what}, which isn't installed.`,
  }
}

/** Torch, soundfile and a new enough transformers: what every Qwen3 model loads through. */
function qwen3Missing(r: SetupReadiness): string[] {
  return [!r.torch && 'torch', !r.transformers_ok && 'transformers', !r.soundfile && 'soundfile'].filter((x): x is string => !!x)
}

export function qwen3Need(r: SetupReadiness | null): Need | null {
  if (!r || r.qwen3_asr) return null
  const missing = qwen3Missing(r)
  return {
    id: 'qwen3', label: 'Qwen3 ASR', action: 'diagnostics', packages: missing,
    size: r.torch ? undefined : GPU_TORCH_SIZE,
    reason: `Qwen3 ASR needs torch, soundfile and transformers 5.15 or newer. Missing: ${names(missing)}.`,
  }
}

export function qwenAlignNeed(r: SetupReadiness | null, language: string): Need | null {
  if (!r) return null
  const base = qwen3Need(r)
  const wordSplitter = language === 'ja' ? (r.nagisa ? null : 'nagisa') : language === 'ko' ? (r.soynlp ? null : 'soynlp') : null
  if (!base && !wordSplitter) return null
  const missing = [...(base?.packages ?? []), ...(wordSplitter ? [wordSplitter] : [])]
  return {
    id: 'qwen3_align', label: 'Qwen3 forced alignment', action: 'diagnostics', packages: missing,
    size: base?.size,
    reason: `Qwen3 forced alignment needs torch, soundfile, transformers 5.15 or newer${language === 'ja' ? ' and nagisa' : language === 'ko' ? ' and soynlp' : ''}. Missing: ${names(missing)}.`,
  }
}

export function realignNeed(r: SetupReadiness | null): Need | null {
  if (!r) return null
  const missing = [!r.torch && 'torch', !r.torchaudio && 'torchaudio', !r.uroman && 'uroman'].filter((x): x is string => !!x)
  if (!missing.length) return null
  return {
    id: 'realign', label: 'Realign long segments', action: 'diagnostics', packages: missing,
    reason: `Realign long segments needs torch, torchaudio and uroman. Missing: ${names(missing)}.`,
  }
}

export function groqNeed(r: SetupReadiness | null): Need | null {
  if (!r || r.key_groq) return null
  return {
    id: 'groq_key', label: 'Groq key', action: 'settings', packages: [],
    reason: 'Use Groq needs a Groq API key. Add it in Settings.',
  }
}

/** Speaker detection: the pyannote package (with torch), then a Hugging Face token. Two needs, two fixes. */
export function speakerNeeds(r: SetupReadiness | null): Need[] {
  if (!r) return []
  const out: Need[] = []
  const missing = [!r.pyannote && 'pyannote.audio', !r.torch && 'torch'].filter((x): x is string => !!x)
  if (missing.length) {
    out.push({
      id: 'speakers_package', label: 'Speaker detection', action: 'diagnostics', packages: missing,
      size: r.torch ? undefined : GPU_TORCH_SIZE,
      reason: `Speaker detection needs pyannote.audio and torch. Missing: ${names(missing)}.`,
    })
  }
  if (!r.hf_token_configured) {
    out.push({
      id: 'speakers_token', label: 'Hugging Face token', action: 'settings', packages: [],
      reason: "Speaker detection needs a Hugging Face token, and you must accept the speaker model's terms on its Hugging Face page.",
      link: { href: SPEAKER_MODEL_URL, label: 'Accept the model terms' },
    })
  }
  return out
}

/** Under the "Transcribe on the GPU" switch: why it changes nothing, or the way to make it faster. */
export function gpuSettingNote(r: SetupReadiness | null): Need | null {
  if (r && !r.gpu_found) {
    return {
      id: 'no_gpu', label: 'GPU', action: 'external', packages: [], recommended: true,
      reason: 'No NVIDIA GPU was found on this PC, so transcription runs on the CPU whatever this is set to.',
    }
  }
  return gpuTorchNeed(r)
}

/** Recommended, never required: an NVIDIA GPU is there but PyTorch cannot use it. */
export function gpuTorchNeed(r: SetupReadiness | null): Need | null {
  if (!r || !r.gpu_found || r.torch_cuda) return null
  return {
    id: 'gpu_torch', label: 'GPU PyTorch', action: 'install_gpu_torch', packages: [], size: GPU_TORCH_SIZE, recommended: true,
    reason: r.torch
      ? 'An NVIDIA GPU was found but PyTorch is CPU-only. Installing GPU PyTorch makes transcription faster.'
      : "An NVIDIA GPU was found but PyTorch isn't installed. Installing GPU PyTorch makes transcription faster.",
  }
}

/** The ASMR voice detector runs on onnxruntime beside faster-whisper; its ~119 MB model downloads from the Voice detector field. */
export function asmrNeed(r: SetupReadiness | null): Need | null {
  if (!r || (r.onnxruntime && r.faster_whisper)) return null
  const missing = [!r.onnxruntime && 'onnxruntime', !r.faster_whisper && 'faster_whisper'].filter((x): x is string => !!x)
  return {
    id: 'asmr', label: 'ASMR voice detector', action: 'diagnostics', packages: missing,
    reason: `The ASMR voice detector needs onnxruntime and Whisper. Missing: ${names(missing)}.`,
  }
}

/** Reading text off pictures: Tesseract (the pytesseract package and its program) or PaddleOCR (two packages). Either is enough. */
export function ocrNeed(r: SetupReadiness | null): Need | null {
  if (!r || (r.pytesseract && r.tesseract_program) || r.paddle_ocr) return null
  return {
    id: 'ocr', label: 'OCR', action: 'diagnostics',
    packages: r.pytesseract ? [] : ['pytesseract'],
    reason: r.pytesseract
      ? "The Tesseract program isn't installed or isn't on the PATH (or install PaddleOCR instead)."
      : "No OCR is installed. Tesseract (the pytesseract package and its program) or PaddleOCR reads on-screen text.",
  }
}

/**
 * What a Scanlate run needs before it can start: OpenCV (every page fails without it) and the chosen
 * OCR backend. `ocrInstalled` is the server's own answer for that backend, which also covers the
 * Tesseract program; readiness only names which package is missing.
 */
export function scanlateNeed(r: SetupReadiness | null, ocrBackend: string, ocrInstalled: boolean): Need | null {
  if (r && !r.cv2) {
    return {
      id: 'scanlate_opencv', label: 'OpenCV', action: 'diagnostics', packages: ['opencv-python'],
      reason: "Translating pages needs opencv-python, which isn't installed.",
    }
  }
  if (ocrInstalled) return null
  if (ocrBackend === 'tesseract' && r?.pytesseract) {
    return {
      id: 'scanlate_ocr', label: 'Tesseract', action: 'diagnostics', packages: [],
      reason: "The Tesseract program isn't installed or isn't on the PATH. Install it, or choose another OCR backend in Settings.",
    }
  }
  const packages = ({
    manga_ocr: ['manga-ocr'], paddle: ['paddleocr', 'paddlepaddle'], tesseract: ['pytesseract'], paddle_vl_manga: ['transformers'],
  } as Record<string, string[]>)[ocrBackend] ?? []
  return {
    id: 'scanlate_ocr', label: 'OCR', action: 'diagnostics', packages,
    reason: `The ${ocrBackend} OCR backend isn't fully installed.${packages.length ? ` Missing: ${names(packages)}.` : ''} Install it, or choose another OCR backend in Settings.`,
  }
}

export const NO_LIBASS = 'Needs an FFmpeg build with libass.'

/** Burning subtitles into the picture goes through ffmpeg's libass; a build without it fails the whole export. */
export function libassNeed(r: SetupReadiness | null): Need | null {
  const ffmpeg = ffmpegNeed(r)
  if (ffmpeg || !r || r.ffmpeg_libass) return ffmpeg
  return {
    id: 'libass', label: 'FFmpeg with libass', action: 'recheck', packages: [],
    reason: "This FFmpeg can't draw subtitles into the picture (it was built without libass). Install a full FFmpeg build, then check again.",
    link: { href: 'https://ffmpeg.org/download.html', label: 'Get FFmpeg' },
  }
}

/** Downloading a live stream goes through yt-dlp (and so does the delayed player's recording). */
export function ytDlpNeed(r: SetupReadiness | null): Need | null {
  if (!r || r.yt_dlp) return null
  return {
    id: 'yt_dlp', label: 'yt-dlp', action: 'diagnostics', packages: ['yt-dlp'],
    reason: "Live needs yt-dlp to read the stream, and it isn't installed.",
  }
}

/**
 * Downloading from a link: ffmpeg always pulls the audio out; a video page (not a plain file link) goes
 * through yt-dlp. Both block Download, since the server refuses before it queues.
 */
export function urlDownloadNeeds(r: SetupReadiness | null, directFileLink: boolean): Need[] {
  const ytDlp = directFileLink ? null : ytDlpNeed(r)
  const out: (Need | null)[] = [
    ytDlp && { ...ytDlp, reason: "Downloading from a video page needs yt-dlp, which isn't installed. A direct link to an audio or video file works without it." },
    ffmpegNeed(r),
  ]
  return out.filter((x): x is Need => !!x)
}

/** Sentence cards still build without FFmpeg, but every card then comes out without its audio clip. */
export function cardAudioNeed(r: SetupReadiness | null): Need | null {
  const ffmpeg = ffmpegNeed(r)
  return ffmpeg && { ...ffmpeg, recommended: true, reason: "FFmpeg isn't installed, so sentence cards will have no audio clips." }
}

/** Some sites (YouTube) only download through yt-dlp when a JavaScript runtime is present. A suggestion, not a requirement. */
export function jsRuntimeNeed(r: SetupReadiness | null): Need | null {
  if (!r || r.js_runtime) return null
  return {
    id: 'js_runtime', label: 'JavaScript runtime', action: 'diagnostics', packages: [], recommended: true,
    reason: "No JavaScript runtime (Deno or Node) was found. YouTube streams may not open without one.",
  }
}

export interface LiveNeedsForm {
  delayedPlayer: boolean
}

export interface LiveNeeds {
  whisper: Need | null
  ffmpeg: Need | null
  ytDlp: Need | null
  jsRuntime: Need | null
}

export function liveNeeds(r: SetupReadiness | null, f: LiveNeedsForm): LiveNeeds {
  return {
    whisper: whisperNeed(r),
    ffmpeg: ffmpegNeed(r),
    ytDlp: ytDlpNeed(r),
    // The delayed player downloads the picture too, which is where the runtime matters most.
    jsRuntime: f.delayedPlayer ? jsRuntimeNeed(r) : null,
  }
}

/** What stops Start: Live cannot hear, read or record without these. */
export function liveBlockers(n: LiveNeeds): Need[] {
  return [n.whisper, n.ffmpeg, n.ytDlp].filter((x): x is Need => !!x)
}

/** Why the GPU box is off: no NVIDIA GPU was found at all. Unknown readiness leaves it usable. */
export function liveGpuReason(r: SetupReadiness | null): string | null {
  return r && !r.gpu_found ? 'No NVIDIA GPU was found on this PC, so Whisper runs on the CPU.' : null
}

const LIVE_SIZE_FLAGS: Record<string, keyof SetupReadiness> = {
  tiny: 'whisper_cached_tiny', base: 'whisper_cached_base', small: 'whisper_cached_small', medium: 'whisper_cached_medium',
  'large-v3-turbo': 'whisper_cached_large_v3_turbo',
}

/**
 * Whether a Live Whisper size can be picked. A model that is on the PC always can. One that is not
 * downloads on first use at the main PC (when Whisper itself is installed), but away from it the
 * server refuses it, so it is switched off there with the reason.
 */
export function liveWhisperSize(r: SetupReadiness | null, size: string, atMainPc: boolean): { reason: string | null; download: boolean } {
  const flag = LIVE_SIZE_FLAGS[size]
  if (!r || !flag || r[flag]) return { reason: null, download: false }
  if (!r.faster_whisper) return { reason: 'Needs Whisper, which is not installed.', download: false }
  if (!atMainPc) return { reason: 'Not downloaded yet. Download it from the main PC first.', download: false }
  return { reason: null, download: true }
}

/** Looking up a page's words splits it into words first: Chinese through jieba, Japanese through Sudachi, Korean through Kiwi. */
export function wordSplitterNeed(r: SetupReadiness | null, language: string): Need | null {
  if (!r) return null
  const table: Record<string, { ready: boolean; name: string; key: string }> = {
    zh: { ready: r.jieba, name: 'jieba', key: 'jieba' },
    ja: { ready: r.sudachi, name: 'Sudachi', key: 'sudachipy' },
    ko: { ready: r.kiwi, name: 'Kiwi', key: 'kiwipiepy' },
  }
  const splitter = table[language]
  if (!splitter || splitter.ready) return null
  return {
    id: 'word_splitter', label: 'Word splitter', action: 'diagnostics', packages: [splitter.key],
    reason: `Looking up words needs ${splitter.name} to split the text, and it isn't installed.`,
  }
}

/** Anki decks are built with genanki; the word list CSV needs nothing. */
export function ankiNeed(r: SetupReadiness | null): Need | null {
  if (!r || r.genanki) return null
  return {
    id: 'anki', label: 'Anki deck export', action: 'diagnostics', packages: ['genanki'],
    reason: "Anki decks (.apkg) need the genanki package, which isn't installed. The word list (.csv) works without it.",
  }
}

/** Audio emotion tags run SenseVoice through funasr. The server reports whether it is installed with the tags themselves. */
export function senseVoiceNeed(installed: boolean): Need | null {
  if (installed) return null
  return {
    id: 'sensevoice', label: 'Audio tags (SenseVoice)', action: 'diagnostics', packages: ['funasr'],
    reason: "Tagging from the audio needs the funasr package, which isn't installed.",
  }
}

/** Ollama is a separate program Baihe talks to; it has no installer here. */
export function ollamaNeed(r: SetupReadiness | null): Need | null {
  if (!r || (r.ollama_reachable && r.ollama_model_pulled)) return null
  return {
    id: 'ollama', label: 'Ollama test engine', action: 'external', packages: [],
    reason: r.ollama_reachable
      ? "Ollama is running but the default model isn't pulled yet. Run \"ollama pull\" for it."
      : "Ollama isn't running or isn't installed. It is a free local translator for testing.",
    link: { href: 'https://ollama.com/download', label: 'Get Ollama' },
  }
}

/** Shown before a Qwen3 backend's first use, installed or not: it downloads a model and runs on torch. */
export function qwen3Warning(forAlignment = false): string {
  return `Qwen3 ${forAlignment ? 'forced alignment' : 'ASR'} downloads its model the first time you use it (${forAlignment ? QWEN3_ALIGNER_SIZE : QWEN3_MODEL_SIZE}) and runs on PyTorch.`
}

export interface TranscribeNeedsForm {
  backend: string
  separateVocals: boolean
  separationBackend: string
  alignmentMethod: string
  realignLong: boolean
  useGroq: boolean
  runDiarize: boolean
  language: string
  /** transcript_mode: "whisper" (heard), "have_transcript" (a transcript you supply is aligned), "hardsub_ocr" (read off the picture). */
  mode: string
}

const haveTranscript = (f: TranscribeNeedsForm) => f.mode === 'have_transcript'

/** Whether this run uses the local Whisper: not with Groq (it replaces that step), nor a Whisper-free backend. */
export function usesLocalWhisper(f: TranscribeNeedsForm): boolean {
  if (f.mode === 'hardsub_ocr') return false
  if (haveTranscript(f)) return f.alignmentMethod === 'whisper_diff'
  return !WHISPER_FREE.includes(f.backend) && !f.useGroq
}

export interface TranscribeNeeds {
  /** Local Whisper is part of this run and missing. */
  whisper: Need | null
  /** Availability of each optional control, whether or not it is switched on. */
  separation: Need | null
  qwen3: Need | null
  qwenAlign: Need | null
  realign: Need | null
  groq: Need | null
  speakers: Need[]
  gpuTorch: Need | null
  ffmpeg: Need | null
}

export function transcribeNeeds(r: SetupReadiness | null, f: TranscribeNeedsForm): TranscribeNeeds {
  return {
    whisper: usesLocalWhisper(f) ? whisperNeed(r) : null,
    separation: separationNeed(r, f.separationBackend),
    qwen3: qwen3Need(r),
    qwenAlign: qwenAlignNeed(r, f.language),
    realign: realignNeed(r),
    groq: groqNeed(r),
    speakers: speakerNeeds(r),
    gpuTorch: gpuTorchNeed(r),
    ffmpeg: ffmpegNeed(r),
  }
}

/**
 * What the settings on screen need and lack: the primary action must not start a run that is certain
 * to fail. A control that is available but off adds nothing; speaker detection only skips a step, so
 * it never blocks, and the GPU suggestion is not a need at all.
 */
export function runBlockers(n: TranscribeNeeds, f: TranscribeNeedsForm): Need[] {
  const out: (Need | null)[] = [n.ffmpeg, n.whisper]
  if (f.separateVocals) out.push(n.separation)
  if (f.mode === 'whisper' && QWEN3_BACKENDS.includes(f.backend)) out.push(n.qwen3)
  if (haveTranscript(f) && f.alignmentMethod === 'qwen3_forced_align') out.push(n.qwenAlign)
  if (f.realignLong) out.push(n.realign)
  if (f.useGroq && f.mode === 'whisper') out.push(n.groq)
  return out.filter((x): x is Need => !!x)
}

/** The ASR backend options a missing install rules out, for the picker (the chosen one stays selectable). */
export function unavailableBackends(n: TranscribeNeeds): string[] {
  return n.qwen3 ? QWEN3_BACKENDS : []
}

/** Review tools that re-hear the audio (Re-transcribe, Auto-tune) or scan it for speech (Timing check, Speech coverage). */
export function audioToolNeed(r: SetupReadiness | null, needsFfmpeg: boolean): Need | null {
  const whisper = whisperNeed(r)
  if (whisper) {
    return { ...whisper, reason: "This needs Transcription (Whisper), which isn't installed. It hears the audio, and its speech detector finds where speech is." }
  }
  return needsFfmpeg ? ffmpegNeed(r) : null
}
