import { describe, expect, it } from 'vitest'

import type { SetupReadiness } from '../../types/setup'
import {
  audioToolNeed, gpuTorchNeed, qwen3Need, qwen3Warning, qwenAlignNeed, realignNeed, runBlockers, separationNeed,
  speakerNeeds, transcribeNeeds, unavailableBackends, usesLocalWhisper, whisperNeed, type TranscribeNeedsForm,
} from './needs'

// A fresh GPU server: almost nothing optional is installed.
const FRESH: SetupReadiness = {
  faster_whisper: false, torch: false, torchaudio: false, uroman: false, soundfile: false, audio_separator: false,
  demucs: false, pyannote: false, onnxruntime: false, openai: false, anthropic: false, yt_dlp: false, pypdf: false,
  genanki: false, ebooklib: false, cv2: false, jieba: false, sudachi: false, kiwi: false, nagisa: false, soynlp: false,
  pytesseract: false, paddle_ocr: false, transformers_ok: false, qwen3_asr: false, ffmpeg: true, ffmpeg_libass: true,
  js_runtime: false, tesseract_program: false, gpu_found: true, torch_cuda: false, hf_token_configured: false,
  key_claude: false, key_deepseek: false, key_gemini: false, key_openai: false, key_groq: false,
  ollama_reachable: false, ollama_model_pulled: false,
}
const READY: SetupReadiness = Object.fromEntries(Object.keys(FRESH).map((k) => [k, true])) as unknown as SetupReadiness

const FORM: TranscribeNeedsForm = {
  backend: 'whisper', separateVocals: false, separationBackend: 'auto', alignmentMethod: 'whisper_diff',
  realignLong: false, useGroq: false, runDiarize: false, language: 'zh', mode: 'whisper',
}

describe('unknown readiness never switches anything off', () => {
  it('returns no need for any control', () => {
    const n = transcribeNeeds(null, FORM)
    expect(Object.values(n).flat().filter(Boolean)).toEqual([])
    expect(runBlockers(n, { ...FORM, separateVocals: true, realignLong: true, useGroq: true })).toEqual([])
    expect(audioToolNeed(null, true)).toBeNull()
  })
})

describe('a fresh install', () => {
  const n = transcribeNeeds(FRESH, FORM)

  it('names Whisper first, with an in-place install', () => {
    expect(n.whisper).toMatchObject({ id: 'whisper', action: 'install_whisper', packages: ['faster_whisper'] })
  })

  it('rules out vocal separation with a reason and the package to open', () => {
    expect(n.separation).toMatchObject({ action: 'diagnostics', packages: ['audio-separator'] })
    expect(n.separation?.reason).toContain('audio-separator (or Demucs)')
    expect(separationNeed(FRESH, 'demucs')?.packages).toEqual(['demucs'])
    expect(separationNeed({ ...FRESH, demucs: true }, 'auto')).toBeNull()
    expect(separationNeed({ ...FRESH, demucs: true }, 'audio_separator')).not.toBeNull()
  })

  it('rules out the three Qwen3 backends and names what is missing', () => {
    expect(unavailableBackends(n)).toEqual(['qwen3_asr', 'qwen3_asr_vad', 'qwen3_asr_long'])
    expect(n.qwen3?.packages).toEqual(['torch', 'transformers', 'soundfile'])
    expect(n.qwen3?.reason).toContain('Missing: torch, transformers and soundfile.')
    expect(n.qwen3?.size).toBe('about 2.5 GB')
  })

  it('needs the word splitter for Japanese and Korean forced alignment', () => {
    expect(qwenAlignNeed({ ...READY, nagisa: false }, 'ja')?.packages).toEqual(['nagisa'])
    expect(qwenAlignNeed({ ...READY, nagisa: false }, 'zh')).toBeNull()
    expect(qwenAlignNeed({ ...READY, soynlp: false }, 'ko')?.packages).toEqual(['soynlp'])
    expect(qwenAlignNeed(FRESH, 'ja')?.packages).toEqual(['torch', 'transformers', 'soundfile', 'nagisa'])
  })

  it('rules out realigning without torch, torchaudio and uroman', () => {
    expect(realignNeed(FRESH)?.packages).toEqual(['torch', 'torchaudio', 'uroman'])
    expect(realignNeed({ ...READY })).toBeNull()
    expect(realignNeed({ ...READY, uroman: false })?.packages).toEqual(['uroman'])
  })

  it('splits speaker detection into a package fix and a token fix', () => {
    const [pkg, token] = speakerNeeds(FRESH)
    expect(pkg).toMatchObject({ id: 'speakers_package', action: 'diagnostics', packages: ['pyannote.audio', 'torch'] })
    expect(token).toMatchObject({ id: 'speakers_token', action: 'settings', packages: [] })
    expect(token.link?.href).toContain('huggingface.co/pyannote/')
    expect(token.reason).toContain('terms')
    expect(speakerNeeds({ ...READY, hf_token_configured: false }).map((x) => x.id)).toEqual(['speakers_token'])
    expect(speakerNeeds({ ...READY, pyannote: false }).map((x) => x.id)).toEqual(['speakers_package'])
  })

  it('asks for a Groq key', () => {
    expect(n.groq).toMatchObject({ id: 'groq_key', action: 'settings' })
  })
})

describe('the GPU PyTorch suggestion', () => {
  it('shows only with a GPU found and a torch without CUDA', () => {
    expect(gpuTorchNeed(FRESH)).toMatchObject({ action: 'install_gpu_torch', recommended: true, size: 'about 2.5 GB' })
    expect(gpuTorchNeed(FRESH)?.reason).toContain("PyTorch isn't installed")
    expect(gpuTorchNeed({ ...FRESH, torch: true })?.reason).toContain('CPU-only')
    expect(gpuTorchNeed({ ...FRESH, gpu_found: false })).toBeNull()
    expect(gpuTorchNeed({ ...FRESH, torch: true, torch_cuda: true })).toBeNull()
  })
})

describe('what blocks the run', () => {
  const blockers = (f: Partial<TranscribeNeedsForm>, r: SetupReadiness = FRESH) =>
    runBlockers(transcribeNeeds(r, { ...FORM, ...f }), { ...FORM, ...f }).map((x) => x.id)

  it('a plain Whisper run on a fresh install is blocked by Whisper only', () => {
    expect(blockers({})).toEqual(['whisper'])
  })

  it('a switched-off control adds nothing', () => {
    expect(blockers({ separateVocals: false, realignLong: false, useGroq: false })).toEqual(['whisper'])
  })

  it('a switched-on control adds its need', () => {
    expect(blockers({ separateVocals: true, realignLong: true })).toEqual(['whisper', 'separation', 'realign'])
  })

  it('Groq on with Whisper missing is NOT blocked by Whisper: Groq replaces that step', () => {
    expect(blockers({ useGroq: true })).toEqual(['groq_key'])
    expect(blockers({ useGroq: true }, { ...FRESH, key_groq: true })).toEqual([])
    expect(usesLocalWhisper({ ...FORM, useGroq: true })).toBe(false)
  })

  it('a Whisper-free backend is not blocked by Whisper', () => {
    expect(blockers({ backend: 'qwen3_asr_long' })).toEqual(['qwen3'])
    expect(blockers({ backend: 'anime_whisper' })).toEqual([])
  })

  it('Qwen3 on Whisper segments needs both', () => {
    expect(blockers({ backend: 'qwen3_asr' })).toEqual(['whisper', 'qwen3'])
  })

  it('a pasted transcript is aligned: Whisper for the diff, the Qwen3 aligner for forced alignment', () => {
    expect(blockers({ mode: 'have_transcript' })).toEqual(['whisper'])
    expect(blockers({ mode: 'have_transcript', alignmentMethod: 'qwen3_forced_align' })).toEqual(['qwen3_align'])
  })

  it('reading hard subtitles off the picture needs no Whisper', () => {
    expect(blockers({ mode: 'hardsub_ocr' })).toEqual([])
  })

  it('speaker detection skips a step rather than failing the run, so it never blocks', () => {
    expect(blockers({ runDiarize: true }, { ...FRESH, faster_whisper: true })).toEqual([])
  })

  it('missing FFmpeg blocks every run', () => {
    expect(blockers({ backend: 'anime_whisper' }, { ...FRESH, ffmpeg: false })).toEqual(['ffmpeg'])
  })

  it('is empty when everything is installed', () => {
    expect(blockers({ separateVocals: true, realignLong: true, useGroq: true, backend: 'qwen3_asr' }, READY)).toEqual([])
  })
})

describe('Review tools that hear or scan the audio', () => {
  it('need Whisper (its Silero detector and decoder), then FFmpeg where they scan', () => {
    expect(audioToolNeed(FRESH, false)?.id).toBe('whisper')
    expect(audioToolNeed({ ...FRESH, faster_whisper: true, ffmpeg: false }, true)?.id).toBe('ffmpeg')
    expect(audioToolNeed({ ...FRESH, faster_whisper: true, ffmpeg: false }, false)).toBeNull()
    expect(audioToolNeed(READY, true)).toBeNull()
  })
})

describe('the Qwen3 warning', () => {
  it('names the download and torch, with the size from the registry', () => {
    expect(qwen3Warning()).toContain('about 4.1 GB')
    expect(qwen3Warning()).toContain('PyTorch')
    expect(qwen3Warning(true)).toContain('about 1.8 GB')
  })
  it('qwen3Need is gone once installed', () => {
    expect(qwen3Need(READY)).toBeNull()
    expect(whisperNeed(READY)).toBeNull()
  })
})
