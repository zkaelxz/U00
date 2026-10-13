import { describe, expect, it } from 'vitest'

import type { SetupReadiness } from '../../types/setup'
import { recommendedRows, recommendedSummary } from './recommended'

const FRESH = Object.fromEntries([
  'faster_whisper', 'torch', 'torchaudio', 'uroman', 'soundfile', 'audio_separator', 'demucs', 'pyannote', 'onnxruntime', 'openai',
  'anthropic', 'yt_dlp', 'pypdf', 'genanki', 'ebooklib', 'cv2', 'jieba', 'sudachi', 'kiwi', 'nagisa', 'soynlp', 'pytesseract',
  'paddle_ocr', 'transformers_ok', 'qwen3_asr', 'ffmpeg_libass', 'js_runtime', 'tesseract_program', 'torch_cuda', 'hf_token_configured',
  'key_claude', 'key_deepseek', 'key_gemini', 'key_openai', 'key_groq', 'ollama_reachable', 'ollama_model_pulled',
].map((k) => [k, false]).concat([['ffmpeg', true], ['gpu_found', true]])) as unknown as SetupReadiness
const READY = Object.fromEntries(Object.keys(FRESH).map((k) => [k, true])) as unknown as SetupReadiness

describe('recommended installs', () => {
  it('lists Whisper first as required, then the optional pieces', () => {
    const rows = recommendedRows(FRESH)
    expect(rows.map((r) => r.id)).toEqual(['whisper', 'gpu_torch', 'separation', 'speakers', 'asmr', 'qwen3', 'ocr', 'ollama'])
    expect(rows.filter((r) => r.required).map((r) => r.id)).toEqual(['whisper'])
    expect(rows.every((r) => !r.ready && r.size && r.why)).toBe(true)
  })

  it('shows GPU PyTorch only when an NVIDIA GPU was found', () => {
    expect(recommendedRows({ ...FRESH, gpu_found: false }).map((r) => r.id)).not.toContain('gpu_torch')
  })

  it('a fully set up PC is all ready with no needs', () => {
    const rows = recommendedRows(READY)
    expect(rows.every((r) => r.ready && r.needs.length === 0)).toBe(true)
    expect(recommendedSummary(rows)).toBe(`${rows.length} of ${rows.length} ready`)
  })

  it('each missing row carries the fix for its cause', () => {
    const by = Object.fromEntries(recommendedRows(FRESH).map((r) => [r.id, r]))
    expect(by.whisper.needs[0].action).toBe('install_whisper')
    expect(by.gpu_torch.needs[0].action).toBe('install_gpu_torch')
    expect(by.separation.needs[0]).toMatchObject({ action: 'diagnostics', packages: ['audio-separator'] })
    expect(by.speakers.needs.map((n) => n.action)).toEqual(['diagnostics', 'settings'])
    expect(by.ollama.needs[0]).toMatchObject({ action: 'external' })
    expect(by.ocr.needs[0].packages).toEqual(['pytesseract'])
  })

  it('OCR is ready with either Tesseract (package and program) or PaddleOCR', () => {
    const ocr = (r: Partial<SetupReadiness>) => recommendedRows({ ...FRESH, ...r }).find((x) => x.id === 'ocr')!
    expect(ocr({ pytesseract: true, tesseract_program: true }).ready).toBe(true)
    expect(ocr({ paddle_ocr: true }).ready).toBe(true)
    expect(ocr({ pytesseract: true }).ready).toBe(false)
    expect(ocr({ pytesseract: true }).needs[0].reason).toContain('Tesseract program')
  })
})
