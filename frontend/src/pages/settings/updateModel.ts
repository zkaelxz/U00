// Settings > App updates: the words and the which-button-when rules, kept
// out of the component so they can be tested on their own.
import type { UpdateStatus } from '../../types/update'
import { formatBytes } from '../libraryAdmin/libraryAdmin'

// The app downloads without a Mark-of-the-Web and starts Setup directly, so
// whether Windows shows a warning is not something to promise either way.
export const SMARTSCREEN_NOTE =
  'The installer is not code-signed. Windows may or may not show a warning before Setup opens.'
export const HASH_NOTE =
  'The download is checked against the SHA-256 published with the release. That catches a broken download; it is not a signature.'
export const INSTALL_NOTE =
  'Opens Setup. Baihe keeps running until you click Install there; then Setup stops Baihe (running jobs are cancelled), updates it and can start it again. Cancelling Setup changes nothing.'
export const AUTO_CHECK_HELP = 'Looks for a newer version once a day. It never downloads or installs on its own.'
export const SOURCE_CHECKOUT_NOTE = 'This copy runs from a source checkout: update it with git.'
export const NO_RELEASE_LINE =
  "No release found. If the project's releases were made private, download the installer by hand from the releases page and check it against its .sha256 file."
export const CUSTOM_SOURCE_NOTE = 'Custom update source: this PC checks a different repository than the default.'

export function versionLine(s: UpdateStatus): string {
  return s.current ? `Version ${s.current}` : 'Source checkout'
}

export function checkLine(s: UpdateStatus): string {
  if (s.check_error) return s.check_error
  if (s.checked_at == null) return 'Not checked yet.'
  if (!s.latest) return NO_RELEASE_LINE
  if (s.update_available) {
    return `Version ${s.latest} is available${s.size ? ` (${formatBytes(s.size)})` : ''}.`
  }
  return s.current ? 'You have the latest version.' : `The latest release is ${s.latest}.`
}

export function downloadLine(s: UpdateStatus): string | null {
  if (s.download === 'downloading') {
    const of = s.size ? ` of ${formatBytes(s.size)}` : ''
    return `Downloading… ${formatBytes(s.downloaded_bytes)}${of}`
  }
  if (s.verified) return `Downloaded and verified: ${s.verified_name ?? 'the installer'}.`
  if (s.download === 'failed') return `${s.download_error ?? 'The download failed.'} You can download it again.`
  return null
}

/** The version Install would start: the verified download's, never the last check's. */
export const installTarget = (s: UpdateStatus): string => `version ${s.verified_version ?? ''}`.trim()

export const canDownload = (s: UpdateStatus): boolean =>
  s.installed && s.update_available && s.download !== 'downloading' && !s.verified

export const isDownloading = (s: UpdateStatus): boolean => s.download === 'downloading'
