import { describe, expect, it } from 'vitest'

import type { SetupReadiness } from '../../types/setup'
import {
  ankiNeed, cardAudioNeed, gpuSettingNote, audioToolNeed, gpuTorchNeed, libassNeed, liveBlockers, gpuOnText, liveGpuReason, liveNeeds, liveWhisperSize, senseVoiceNeed, wordSplitterNeed, ytDlpNeed, qwen3Need, qwen3Warning, qwenAlignNeed, realignNeed, runBlockers, separationNeed,
  browserTierNeed, mlDetectorNeed, notifyNeed, ocrBackendNeed, trafilaturaNeed,
  scanlateNeed, speakerNeeds, transcribeNeeds, unavailableBackends, urlDownloadNeeds, usesLocalWhisper, whisperNeed, type TranscribeNeedsForm,
} from './needs'

// A fresh GPU server: almost nothing optional is installed.
const FRESH: SetupReadiness = {
  faster_whisper: false, torch: false, torchaudio: false, uroman: false, soundfile: false, audio_separator: false,
  demucs: false, pyannote: false, onnxruntime: false, openai: false, anthropic: false, yt_dlp: false, pypdf: false,
  genanki: false, ebooklib: false, cv2: false, jieba: false, sudachi: false, kiwi: false, nagisa: false, soynlp: false,
  pytesseract: false, paddle_ocr: false, transformers_ok: false, qwen3_asr: false, ffmpeg: true, ffmpeg_libass: true,
  js_runtime: false, tesseract_program: false, manga_ocr: false, plyer: false, trafilatura: false, playwright: false,
  browser_found: false, gpu_found: true, torch_cuda: false, hf_token_configured: false,
  key_claude: false, key_deepseek: false, key_gemini: false, key_openai: false, key_groq: false,
  ollama_reachable: false, ollama_model_pulled: false,
  whisper_cached_tiny: false, whisper_cached_base: false, whisper_cached_small: false, whisper_cached_medium: false,
  whisper_cached_large_v3_turbo: false, whisper_cached_large_v3: false,
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

describe('scanlateNeed', () => {
  it('never blocks on unknown readiness while the backend is installed', () => {
    expect(scanlateNeed(null, 'manga_ocr', true)).toBeNull()
    expect(scanlateNeed(READY, 'manga_ocr', true)).toBeNull()
  })
  it('names OpenCV first', () => {
    expect(scanlateNeed({ ...READY, cv2: false }, 'tesseract', false)).toMatchObject({
      id: 'scanlate_opencv', packages: ['opencv-python'],
    })
  })
  it('says the Tesseract program is missing when only the package is there', () => {
    const n = scanlateNeed({ ...READY, tesseract_program: false }, 'tesseract', false)
    expect(n).toMatchObject({ id: 'scanlate_ocr', packages: [] })
    expect(n?.reason).toContain('Tesseract program')
  })
  it('lists the packages a backend lacks', () => {
    expect(scanlateNeed(null, 'paddle', false)?.packages).toEqual(['paddleocr', 'paddlepaddle'])
    expect(scanlateNeed(null, 'manga_ocr', false)?.packages).toEqual(['manga-ocr'])
  })
})

describe('Export, Live, Reader and Review needs', () => {
  it('stay silent while readiness is unknown', () => {
    expect(libassNeed(null)).toBeNull()
    expect(ytDlpNeed(null)).toBeNull()
    expect(wordSplitterNeed(null, 'zh')).toBeNull()
    expect(ankiNeed(null)).toBeNull()
    expect(liveGpuReason(null)).toBeNull()
    expect(liveWhisperSize(null, 'medium', false)).toEqual({ reason: null, download: false })
    expect(liveBlockers(liveNeeds(null, { delayedPlayer: true }))).toEqual([])
  })

  it('burned-in export names FFmpeg first, then the missing libass', () => {
    expect(libassNeed({ ...READY, ffmpeg: false })).toMatchObject({ id: 'ffmpeg' })
    expect(libassNeed({ ...READY, ffmpeg_libass: false })).toMatchObject({ id: 'libass', action: 'recheck' })
    expect(libassNeed(READY)).toBeNull()
  })

  it('Live stops on Whisper, FFmpeg and yt-dlp, and only suggests a JavaScript runtime for the delayed player', () => {
    const bare = { ...FRESH, ffmpeg: false }
    expect(liveBlockers(liveNeeds(bare, { delayedPlayer: false })).map((n) => n.id)).toEqual(['whisper', 'ffmpeg', 'yt_dlp'])
    expect(liveNeeds(bare, { delayedPlayer: false }).jsRuntime).toBeNull()
    expect(liveNeeds(bare, { delayedPlayer: true }).jsRuntime).toMatchObject({ recommended: true })
    expect(liveBlockers(liveNeeds(READY, { delayedPlayer: true }))).toEqual([])
  })

  it('switches the GPU box off only when no NVIDIA GPU exists', () => {
    expect(liveGpuReason({ ...READY, gpu_found: false })).toContain('No NVIDIA GPU')
    expect(liveGpuReason({ ...READY, torch_cuda: false })).toBeNull()
  })

  it('offers a Whisper size that is on the PC, downloadable at the PC, and refuses the rest away from it', () => {
    const r = { ...READY, whisper_cached_medium: false }
    expect(liveWhisperSize(r, 'small', false)).toEqual({ reason: null, download: false })
    expect(liveWhisperSize(r, 'medium', true)).toEqual({ reason: null, download: true })
    expect(liveWhisperSize(r, 'medium', false).reason).toContain('main PC')
    expect(liveWhisperSize({ ...r, faster_whisper: false }, 'medium', true).reason).toContain('Whisper')
  })

  it('names the word splitter for the title language', () => {
    expect(wordSplitterNeed({ ...READY, jieba: false }, 'zh')).toMatchObject({ packages: ['jieba'] })
    expect(wordSplitterNeed({ ...READY, sudachi: false }, 'ja')).toMatchObject({ packages: ['sudachipy'] })
    expect(wordSplitterNeed({ ...READY, kiwi: false }, 'ko')).toMatchObject({ packages: ['kiwipiepy'] })
    expect(wordSplitterNeed({ ...READY, jieba: false }, 'ja')).toBeNull()
    expect(wordSplitterNeed({ ...READY, jieba: false }, 'en')).toBeNull()
  })

  it('names genanki for Anki decks and funasr for audio tags', () => {
    expect(ankiNeed({ ...READY, genanki: false })).toMatchObject({ packages: ['genanki'] })
    expect(ankiNeed(READY)).toBeNull()
    expect(senseVoiceNeed(false)).toMatchObject({ packages: ['funasr'], action: 'diagnostics' })
    expect(senseVoiceNeed(true)).toBeNull()
  })
})

describe('urlDownloadNeeds', () => {
  const ready = { ffmpeg: true, yt_dlp: true } as SetupReadiness
  it('needs nothing when unknown or installed', () => {
    expect(urlDownloadNeeds(null, false)).toEqual([])
    expect(urlDownloadNeeds(ready, false)).toEqual([])
  })
  it('names yt-dlp for a video page but not for a plain file link', () => {
    const r = { ...ready, yt_dlp: false }
    expect(urlDownloadNeeds(r, false).map((n) => n.id)).toEqual(['yt_dlp'])
    expect(urlDownloadNeeds(r, true)).toEqual([])
  })
  it('names ffmpeg for either kind of link', () => {
    const r = { ...ready, ffmpeg: false }
    expect(urlDownloadNeeds(r, true).map((n) => n.id)).toEqual(['ffmpeg'])
    expect(urlDownloadNeeds(r, false).map((n) => n.id)).toEqual(['ffmpeg'])
  })
})

describe('cardAudioNeed', () => {
  it('is a suggestion when FFmpeg is missing and silent when unknown or present', () => {
    expect(cardAudioNeed(null)).toBeNull()
    expect(cardAudioNeed({ ffmpeg: true } as SetupReadiness)).toBeNull()
    const n = cardAudioNeed({ ffmpeg: false } as SetupReadiness)
    expect(n?.recommended).toBe(true)
    expect(n?.reason).toContain('no audio clips')
  })
})

describe('gpuSettingNote', () => {
  it('says the switch changes nothing without a GPU, and offers GPU PyTorch when only that is missing', () => {
    expect(gpuSettingNote(null)).toBeNull()
    expect(gpuSettingNote({ gpu_found: false } as SetupReadiness)?.reason).toContain('runs on the CPU')
    expect(gpuSettingNote({ gpu_found: true, torch_cuda: false, torch: true } as SetupReadiness)?.id).toBe('gpu_torch')
    expect(gpuSettingNote({ gpu_found: true, torch_cuda: true } as SetupReadiness)).toBeNull()
  })
})

describe('notifyNeed', () => {
  it('says nothing while readiness is unknown or plyer is present', () => {
    expect(notifyNeed(null)).toBeNull()
    expect(notifyNeed(READY)).toBeNull()
  })

  it('names plyer for the popup and says the channels do not need it', () => {
    const n = notifyNeed(FRESH)!
    expect(n.packages).toEqual(['plyer'])
    expect(n.recommended).toBe(true)
    expect(n.reason).toContain('Discord and ntfy')
  })
})

describe('install notes for the OCR, comic detector and Sources surfaces', () => {
  it('say nothing while readiness is unknown or everything is present', () => {
    expect([trafilaturaNeed(null), browserTierNeed(null), ocrBackendNeed(null, 'paddle'), mlDetectorNeed(null, 'ml', false)]).toEqual([null, null, null, null])
    expect([trafilaturaNeed(READY), browserTierNeed(READY), ocrBackendNeed(READY, 'paddle'), mlDetectorNeed(READY, 'ml', true)]).toEqual([null, null, null, null])
  })
  it('names each OCR backend\'s own missing piece', () => {
    expect(ocrBackendNeed(FRESH, 'manga_ocr')!.packages).toEqual(['manga-ocr'])
    expect(ocrBackendNeed(FRESH, 'paddle')!.packages).toEqual(['paddleocr', 'paddlepaddle'])
    expect(ocrBackendNeed(FRESH, 'tesseract')!.packages).toEqual(['pytesseract'])
    expect(ocrBackendNeed({ ...FRESH, pytesseract: true }, 'tesseract')!.reason).toContain('program')
    expect(ocrBackendNeed(FRESH, 'auto')).toBeNull()
    expect(ocrBackendNeed(FRESH, 'paddle_vl_manga')).toBeNull()
  })

  it('asks for the package before the browser, and for a recommended extractor calmly', () => {
    expect(browserTierNeed(FRESH)!.packages).toEqual(['playwright'])
    const noBrowser = browserTierNeed({ ...FRESH, playwright: true })!
    expect(noBrowser.id).toBe('browser_program')
    expect(noBrowser.packages).toEqual([])
    expect(trafilaturaNeed(FRESH)!.recommended).toBe(true)
  })

  it('explains the OpenCV fallback of the ML text detector', () => {
    expect(mlDetectorNeed(FRESH, 'auto', false)!.packages).toEqual(['torch'])
    const ready = { ...READY }
    expect(mlDetectorNeed(ready, 'cv', false)).toBeNull()
    expect(mlDetectorNeed(ready, 'ml', false)!.reason).toContain('downloads on the first run')
    expect(mlDetectorNeed(ready, 'auto', false)!.reason).toContain('Choose "ML model"')
    expect(mlDetectorNeed(ready, 'auto', true)).toBeNull()
    expect(mlDetectorNeed(ready, 'auto', undefined)).toBeNull()
  })
})

describe('gpuOnText', () => {
  it('reads the setting, and adds "none found" only when readiness found no GPU', () => {
    expect(gpuOnText(false, { ...READY, gpu_found: false })).toBe('GPU off')
    expect(gpuOnText(true, null)).toBe('GPU on')
    expect(gpuOnText(true, { ...READY, gpu_found: true })).toBe('GPU on')
    expect(gpuOnText(true, { ...READY, gpu_found: false })).toBe('GPU on, none found')
  })
})
