// Pure helpers for the "Install ffmpeg" card (GET /api/diagnostics/ffmpeg).
import type { FfmpegInstallStatus } from '../../types/ffmpegInstall'
import { jobRunning } from './jobPoll'

export const FFMPEG_INTRO =
  'ffmpeg reads audio and video and burns subtitles into a picture. This downloads the vendor build into ' +
  "Baihe's own folder and checks it against a fixed SHA-256. Nothing is installed system-wide and the " +
  'Windows PATH is not changed. The build is GPL v3 (gyan.dev "essentials").'

export const ffmpegConfirm = (s: FfmpegInstallStatus) => `Confirm install ffmpeg (about ${s.download_mb} MB)`

/** Show the card: ffmpeg is missing, or a job or result is there to report. */
export const showFfmpeg = (s: FfmpegInstallStatus | null): boolean =>
  !!s && (!s.found || jobRunning(s.job) || !!s.last_result)

/** Can the install button be offered (ignoring other running jobs)? */
export const canOfferFfmpeg = (s: FfmpegInstallStatus): boolean => !s.refusal && !jobRunning(s.job)

/** The state in one line, or null while a job runs. */
export function ffmpegStatusLine(s: FfmpegInstallStatus): string | null {
  if (jobRunning(s.job)) return null
  if (s.app_ffmpeg_installed) return 'ffmpeg is installed in the app\'s own folder.'
  return s.refusal ?? null
}

type ResultLine = { text: string; tone: 'ok' | 'error' }

/** The last install's outcome, or null. */
export function ffmpegResultLine(s: FfmpegInstallStatus): ResultLine | null {
  const r = s.last_result
  if (!r || jobRunning(s.job)) return null
  return r.ok ? { text: r.message || 'ffmpeg installed.', tone: 'ok' }
    : { text: r.message || 'The ffmpeg install failed.', tone: 'error' }
}
