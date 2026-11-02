// Pure helpers for the "Voice cloning: <engine>" rows (GET /api/diagnostics/voice-engines).
import type { VoiceEngineInstallRow } from '../../types/voiceEngines'
import { jobRunning } from './jobPoll'

/** The engines that install into a folder of their own; `gb` is the disk the text quotes. */
export const VOICE_ENGINE_CARDS = {
  qwen3_tts: { card: 'qwen3-tts', label: 'Qwen3-TTS', gb: 5, note: null },
  chatterbox: {
    card: 'chatterbox', label: 'Chatterbox', gb: 8,
    // chatterbox-tts always marks its output with Resemble AI's Perth watermark.
    note: 'Chatterbox adds an inaudible watermark to everything it speaks.',
  },
} as const

export type VoiceEngineKey = keyof typeof VOICE_ENGINE_CARDS

export const VOICE_ENGINE_INTRO = 'Clone a voice locally. Installs into its own folder so it does not change your other packages.'
export const voiceEngineDiskLine = (label: string, gb: number) =>
  `${label} needs about ${gb} GB of disk space and downloads its model on first use.`
export const voiceEngineRemoveNote = (label: string, gb: number) =>
  `Remove ${label}? This deletes its separate folder (about ${gb} GB). Your voices and narration tracks are kept.`
export const voiceEngineDamagedLine = (label: string) =>
  `The ${label} folder is damaged. Remove it in Diagnostics and install it again.`

/** Can Remove be offered: its folder exists and nothing of it runs. */
export const canRemoveVoiceEngine = (r: VoiceEngineInstallRow): boolean => r.folder_present && !jobRunning(r.job)

/** Can the install button be offered (ignoring other running jobs)? */
export const canOfferVoiceEngine = (r: VoiceEngineInstallRow): boolean => !r.refusal && !jobRunning(r.job)

/** The state in one line, or null while a job runs. */
export function voiceEngineStatusLine(r: VoiceEngineInstallRow): string | null {
  if (jobRunning(r.job)) return null
  if (r.installed) return `${r.label} is installed.`
  if (r.folder_present) return voiceEngineDamagedLine(r.label)
  return r.refusal ?? null
}

type ResultLine = { text: string; tone: 'ok' | 'error' }

/** The last install's outcome, or null. */
export function voiceEngineResultLine(r: VoiceEngineInstallRow): ResultLine | null {
  const last = r.last_result
  if (!last || jobRunning(r.job)) return null
  return { text: last.message, tone: last.ok ? 'ok' : 'error' }
}
