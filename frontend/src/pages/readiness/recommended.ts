// The rows of Diagnostics' "Recommended installs" card: for each optional piece, whether it is ready, why
// you would want it, how big it is, and the needs (with their fixes) when it is not. Pure of React.
import type { SetupReadiness } from '../../types/setup'
import {
  asmrNeed, gpuTorchNeed, ocrNeed, ollamaNeed, qwen3Need, separationNeed, speakerNeeds, whisperNeed, GPU_TORCH_SIZE, QWEN3_MODEL_SIZE,
  type Need,
} from './needs'

interface RecommendedRow {
  id: string
  label: string
  /** Required to make subtitles at all (Whisper); everything else is optional. */
  required: boolean
  ready: boolean
  why: string
  size: string
  needs: Need[]
}

const row = (id: string, label: string, required: boolean, why: string, size: string, needs: (Need | null)[]): RecommendedRow => {
  const open = needs.filter((n): n is Need => !!n)
  return { id, label, required, ready: open.length === 0, why, size, needs: open }
}

/** Whisper, GPU PyTorch (only with an NVIDIA GPU), then the optional extras. */
export function recommendedRows(r: SetupReadiness): RecommendedRow[] {
  const rows = [
    row('whisper', 'Whisper (transcription)', true, 'Turns speech into timed lines. Nothing else works without it.',
      'about 80 MB, plus a 1.6 to 3 GB model on first use', [whisperNeed(r)]),
  ]
  if (r.gpu_found) {
    rows.push(row('gpu_torch', 'GPU PyTorch', false, 'Makes transcription, vocal separation and speaker detection much faster on your NVIDIA GPU.',
      GPU_TORCH_SIZE, [gpuTorchNeed(r)]))
  }
  rows.push(
    row('separation', 'Vocal separation', false, 'Removes background music before transcribing (audio-separator).',
      'about 30 MB, plus a model on first use', [separationNeed(r, 'audio_separator')]),
    row('speakers', 'Speaker detection', false, 'Tells speakers apart (pyannote). Needs a Hugging Face token and accepted model terms.',
      r.torch ? 'about 20 MB, plus a model on first use' : `about 20 MB, plus PyTorch (${GPU_TORCH_SIZE})`, speakerNeeds(r)),
    row('asmr', 'ASMR voice detector', false, 'Finds soft and whispered speech in Japanese ASMR.',
      'about 15 MB, plus a model of about 119 MB', [asmrNeed(r)]),
    row('qwen3', 'Qwen3 ASR', false, 'A second speech recogniser that can read speech Whisper skips.',
      `${QWEN3_MODEL_SIZE} model, plus PyTorch if missing`, [qwen3Need(r)]),
    row('ocr', 'OCR (Tesseract or PaddleOCR)', false, 'Reads burned-in subtitles and text on comic pages.',
      'Tesseract is small; PaddleOCR is about 800 MB', [ocrNeed(r)]),
    row('ollama', 'Ollama test engine', false, 'A free translator that runs on this PC, for trying things without a paid key.',
      'a separate program; models are 1 to 20 GB', [ollamaNeed(r)]),
  )
  return rows
}

/** "N of M ready" for the card's summary line. */
export function recommendedSummary(rows: RecommendedRow[]): string {
  const ready = rows.filter((x) => x.ready).length
  return `${ready} of ${rows.length} ready`
}
