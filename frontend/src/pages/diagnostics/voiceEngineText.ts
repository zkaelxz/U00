// Pure helpers for the "Voice cloning: Qwen3-TTS" row (GET /api/diagnostics/voice-engines).
import type { VoiceEngineInstallRow } from '../../types/voiceEngines'
import { jobRunning } from './jobPoll'

export const QWEN3_TTS_INTRO = 'Clone a voice locally. Installs into its own folder so it does not change your other packages.'
export const QWEN3_TTS_DISK = 'Qwen3-TTS needs about 5 GB of disk space and downloads its model on first use.'
export const QWEN3_TTS_REMOVE_NOTE = 'Remove Qwen3-TTS? This deletes its separate folder (about 5 GB). Your voices and narration tracks are kept.'
export const QWEN3_TTS_DAMAGED = 'The Qwen3-TTS folder is damaged. Remove it in Diagnostics and install it again.'

/** Can Remove be offered: its folder exists and nothing of it runs. */
export const canRemoveVoiceEngine = (r: VoiceEngineInstallRow): boolean => r.folder_present && !jobRunning(r.job)

/** Can the install button be offered (ignoring other running jobs)? */
export const canOfferVoiceEngine = (r: VoiceEngineInstallRow): boolean => !r.refusal && !jobRunning(r.job)

/** The state in one line, or null while a job runs. */
export function voiceEngineStatusLine(r: VoiceEngineInstallRow): string | null {
  if (jobRunning(r.job)) return null
  if (r.installed) return `${r.label} is installed.`
  if (r.folder_present) return QWEN3_TTS_DAMAGED
  return r.refusal ?? null
}

type ResultLine = { text: string; tone: 'ok' | 'error' }

/** The last install's outcome, or null. */
export function voiceEngineResultLine(r: VoiceEngineInstallRow): ResultLine | null {
  const last = r.last_result
  if (!last || jobRunning(r.job)) return null
  return { text: last.message, tone: last.ok ? 'ok' : 'error' }
}
