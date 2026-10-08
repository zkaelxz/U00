import type { TranscribeConfigUpdate } from '../../../types/workspace'

// The form shows the computed default backend for a title that never saved one;
// sending it back on any other edit would store it as the user's own choice.
export function withoutUntouchedBackend(
  update: TranscribeConfigUpdate,
  shownBackend: string,
): TranscribeConfigUpdate {
  if (update.asr_backend_choice !== shownBackend) return update
  const { asr_backend_choice: _untouched, ...rest } = update
  return rest
}

// One line per ASR backend id the dropdown can offer (api/asrOptions.ts), kept
// here so the help text is edited in one place. transcribeBackendField.test.ts
// fails if a backend is offered without a line.
export const ASR_BACKEND_HELP: Record<string, string> = {
  whisper: 'Whisper: the default and fastest. Runs on your PC; may skip quiet speech.',
  qwen3_asr: 'Qwen3 ASR: Whisper cuts the lines, Qwen3 rewrites the text. Slower; downloads a model.',
  qwen3_asr_vad: 'Qwen3 ASR with speech detection: no Whisper; lines are cut at speech pauses. Downloads a model.',
  qwen3_asr_long: 'Qwen3 ASR on long windows: no Whisper; keeps quiet speech, one line per sentence. Default for Chinese and Japanese when installed.',
}

export function asrBackendHelp(options: string[]): string {
  return options.map((o) => ASR_BACKEND_HELP[o]).filter(Boolean).join('\n')
}

// What the Use Groq toggle means. Groq replaces only the local Whisper step
// (services/transcribe_service.py): the Qwen3 ASR backend still re-reads
// Groq's lines on this PC, while the speech-detection and long-window
// backends run locally and ignore Groq.
export const GROQ_HELP = [
  "Your audio is uploaded to Groq's servers, which run Whisper for you. It is a rented cloud service, not your PC.",
  'Needs a Groq API key in Settings.',
  'The audio leaves your computer: do not use it for material you want to keep private.',
  'Local Whisper settings (model, beam size, speech detection) are not used. Qwen3 ASR still re-reads the lines on your PC; the other backends ignore Groq.',
].join('\n')
