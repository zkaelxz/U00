// Settings > App updates: the words and the which-button-when rules, kept
// out of the component so they can be tested on their own.
import type { UpdateStatus } from '../../types/update'
import { formatBytes } from '../libraryAdmin/libraryAdmin'

export const SMARTSCREEN_NOTE =
  'The installer is not code-signed, so Windows SmartScreen may say "Windows protected your PC". Click More info, then Run anyway.'
export const HASH_NOTE =
  'The download is checked against the SHA-256 published with the release. That catches a broken download; it is not a signature.'
export const INSTALL_NOTE =
  'Opens Setup. When you click Install there, Setup stops Baihe (running jobs are cancelled), updates it and can start it again.'
export const AUTO_CHECK_HELP = 'Looks for a newer version once a day. It never downloads or installs on its own.'
export const SOURCE_CHECKOUT_NOTE = 'This copy runs from a source checkout: update it with git.'

export function versionLine(s: UpdateStatus): string {
  return s.current ? `Version ${s.current}` : 'Source checkout'
}

export function checkLine(s: UpdateStatus): string {
  if (s.check_error) return s.check_error
  if (s.checked_at == null) return 'Not checked yet.'
  if (!s.latest) return 'No release to offer yet.'
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
  if (s.verified) return `Downloaded and verified: ${s.installer_name ?? 'the installer'}.`
  if (s.download === 'failed') return s.download_error ?? 'The download failed.'
  return null
}

export const canDownload = (s: UpdateStatus): boolean =>
  s.installed && s.update_available && s.download !== 'downloading' && !s.verified

export const isDownloading = (s: UpdateStatus): boolean => s.download === 'downloading'
