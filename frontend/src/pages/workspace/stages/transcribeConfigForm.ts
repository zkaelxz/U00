import { humanizeValue } from '../../../components/labels'
import type { TranscribeConfig, TranscribeConfigUpdate } from '../../../types/workspace'

/** The saved transcription options as the form holds them: numbers stay text until they are checked. */
export type ConfigForm = {
  whisper_size: string
  alignment_method: string
  asr_backend_choice: string
  separation_backend: string
  hardsub_ocr_backend: string
  beam_size: string
  min_silence_ms: string
  min_pause_sec: string
  vad_threshold: string
  sensitivity_preset: string
  hallucination_silence_sec: string
  hardsub_interval_sec: string
  separate_vocals_first: boolean
  realign_long_segments: boolean
  whisper_fast_mode: boolean
  whisper_repeat_guard: boolean
  split_by_sentences: boolean
  vocabulary_hint: boolean
  use_groq: boolean
}

export const formFromConfig = (c: TranscribeConfig): ConfigForm => ({
  whisper_size: c.whisper_size,
  alignment_method: c.alignment_method,
  asr_backend_choice: c.asr_backend_choice,
  separation_backend: c.separation_backend,
  hardsub_ocr_backend: c.hardsub_ocr_backend,
  beam_size: String(c.beam_size),
  min_silence_ms: String(c.min_silence_ms),
  min_pause_sec: String(c.min_pause_sec),
  vad_threshold: String(c.vad_threshold),
  sensitivity_preset: c.sensitivity_preset,
  hallucination_silence_sec: String(c.hallucination_silence_sec),
  hardsub_interval_sec: String(c.hardsub_interval_sec),
  separate_vocals_first: c.separate_vocals_first,
  realign_long_segments: c.realign_long_segments,
  whisper_fast_mode: c.whisper_fast_mode,
  whisper_repeat_guard: c.whisper_repeat_guard ?? false,
  split_by_sentences: c.split_by_sentences ?? false,
  vocabulary_hint: c.vocabulary_hint ?? false,
  use_groq: c.use_groq,
})

export const toUpdate = (f: ConfigForm): TranscribeConfigUpdate => ({
  ...f,
  beam_size: Number(f.beam_size),
  min_silence_ms: Number(f.min_silence_ms),
  min_pause_sec: Number(f.min_pause_sec),
  vad_threshold: Number(f.vad_threshold),
  hallucination_silence_sec: Number(f.hallucination_silence_sec),
  hardsub_interval_sec: Number(f.hardsub_interval_sec),
})

// Display names for the Advanced backend choices (the option value stays raw).
const OPTION_LABELS: Record<string, string> = {
  whisper_diff: 'Whisper (diff)',
  qwen3_forced_align: 'Qwen3 forced alignment',
  whisper: 'Whisper',
  qwen3_asr: 'Qwen3 ASR',
  qwen3_asr_vad: 'Qwen3 ASR with speech detection (no Whisper)',
  qwen3_asr_long: 'Qwen3 ASR on long windows (no Whisper)',
  anime_whisper: 'Anime-whisper (experimental, non-commercial use only)',
  auto: 'Automatic',
  normal: 'Normal (default)',
  sensitive: 'More sensitive',
  audio_separator: 'Audio separator',
  demucs: 'Demucs',
  tesseract: 'Tesseract',
  paddle: 'PaddleOCR',
}
export const optionLabel = (o: string) => OPTION_LABELS[o] ?? humanizeValue(o)

// advancedSummary names changed backends by their raw value; show their labels.
export const readableSummary = (summary: string) =>
  summary
    .split(' · ')
    .map((p) => (p.startsWith('separation ') ? `separation ${optionLabel(p.slice(11))}` : (OPTION_LABELS[p] ?? p)))
    .join(' · ')

// "3 expected", "2-4", or "auto": the Speakers group's one-line summary.
export function speakersSummary(expected: string, min: string, max: string): string {
  if (expected.trim()) return `${expected.trim()} expected`
  if (min.trim() || max.trim()) return `${min.trim() || '?'}-${max.trim() || '?'}`
  return 'auto'
}
