// Settings > App updates: the words and the which-button-when rules, kept
// out of the component so they can be tested on their own.
import type { UpdateStatus } from '../../types/update'
import { formatBytes } from '../libraryAdmin/libraryAdmin'

// The app downloads without a Mark-of-the-Web and starts Setup directly, so
// whether Windows shows a warning is not something to promise either way.
export const SMARTSCREEN_NOTE =
  'The installer is not code-signed. Windows may or may not show a warning before Setup opens.'
export const HASH_NOTE =
  'The download is checked against the release’s SHA-256. That catches a broken download; it is not a signature.'
export const INSTALL_NOTE =
  'Opens Setup. Baihe keeps running until you click Install there. Then Setup stops Baihe (running jobs are cancelled), updates it and can restart it. Cancelling Setup changes nothing.'
export const AUTO_CHECK_HELP = 'Looks for a newer version once a day. It never downloads or installs on its own.'
export const SOURCE_CHECKOUT_NOTE = 'This copy runs from a source checkout: update it with git.'
export const NO_INSTALLER_RELEASE_LINE = 'No installer release has been published yet.'
export const NO_RELEASE_LINE =
  "No release found. If releases were made private, download the installer by hand and check it against its .sha256 file."
export const SIGNATURE_VERIFIED_LINE = 'Signature verified.'
export const SIGNATURE_REQUIRED_LINE = 'Updates must carry a valid release signature.'
export const UNSIGNED_LINE = 'Not signature-checked: this copy has no release key.'
export const CUSTOM_SOURCE_NOTE = 'Custom update source: this PC checks a different repository.'

export function versionLine(s: UpdateStatus): string {
  return s.current ? `Version ${s.current}` : 'Source checkout'
}

export function checkLine(s: UpdateStatus): string {
  if (s.check_error) return s.check_error
  if (s.checked_at == null) return 'Not checked yet.'
  if (!s.latest) return s.release_lookup === 'not_found' ? NO_RELEASE_LINE : NO_INSTALLER_RELEASE_LINE
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

export function signatureLine(s: UpdateStatus): string {
  if (!s.signature_check) return UNSIGNED_LINE
  return s.verified && s.signature_verified ? SIGNATURE_VERIFIED_LINE : SIGNATURE_REQUIRED_LINE
}

/** The version Install would start: the verified download's, never the last check's. */
export const installTarget = (s: UpdateStatus): string => `version ${s.verified_version ?? ''}`.trim()

export const canDownload = (s: UpdateStatus): boolean =>
  s.installed && s.update_available && s.download !== 'downloading' && !s.verified

export const isDownloading = (s: UpdateStatus): boolean =>
  s.download === 'downloading' || s.app_download === 'downloading'

export const APP_APPLY_NOTE =
  'Stops Baihe (running jobs must finish first), swaps in the new app folder and starts it again. ' +
  'If the new version doesn’t start, Baihe goes back to this one by itself. Your library and settings are not touched.'
export const APP_FULL_INSTALLER_LINE =
  'This update changes the bundled Python or its packages, so it needs the full installer.'
export const APP_BOOT_SERVICE_LINE =
  'Baihe runs as the background service here, so this update needs the full installer.'
export const APP_RESTARTING_LINE =
  'Updating the app: Baihe stops and starts again. Reload this page in a minute.'

/** What the app-only path offers for the newer release, or null when it has nothing to say. */
export function appUpdateLine(s: UpdateStatus): string | null {
  if (!s.installed) return null
  if (s.app_update === 'needs_full_installer') return APP_FULL_INSTALLER_LINE
  if (s.app_update !== 'available') return null
  if (!s.app_update_here) return APP_BOOT_SERVICE_LINE
  return `The app can be updated on its own${s.app_size ? ` (${formatBytes(s.app_size)})` : ''}, without the full installer.`
}

export const canDownloadApp = (s: UpdateStatus): boolean =>
  s.installed && s.app_update === 'available' && s.app_update_here && s.app_download !== 'downloading' &&
  !s.app_verified

export function appDownloadLine(s: UpdateStatus): string | null {
  if (s.app_download === 'downloading') {
    const of = s.app_size ? ` of ${formatBytes(s.app_size)}` : ''
    return `Downloading the app update… ${formatBytes(s.app_downloaded_bytes)}${of}`
  }
  if (s.app_verified) {
    return s.app_verified_version
      ? `App update ${s.app_verified_version} downloaded and verified.`
      : 'App update downloaded and verified.'
  }
  if (s.app_download === 'failed') {
    return `${s.app_download_error ?? 'The app update download failed.'} You can download it again.`
  }
  return null
}

export const appApplyTarget = (s: UpdateStatus): string => `version ${s.app_verified_version ?? ''}`.trim()
