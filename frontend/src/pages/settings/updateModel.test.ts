import { afterEach, describe, expect, it, vi } from 'vitest'

import { getPcMode, resetPcModeForTests } from '../../api/pcOnly'
import { applyAppUpdate, checkForUpdates, downloadAppUpdate, installUpdate, setUpdateAutoCheck } from '../../api/update'
import type { UpdateStatus } from '../../types/update'
import {
  APP_BOOT_SERVICE_LINE, APP_FULL_INSTALLER_LINE, appApplyTarget, appDownloadLine, appUpdateLine, canDownloadApp,
  NO_INSTALLER_RELEASE_LINE, NO_RELEASE_LINE, SIGNATURE_REQUIRED_LINE, SIGNATURE_VERIFIED_LINE, SMARTSCREEN_NOTE, UNSIGNED_LINE,
  canDownload, checkLine, downloadLine, installTarget, isDownloading, signatureLine, versionLine,
} from './updateModel'

const BASE: UpdateStatus = {
  current: '0.1.0', installed: true, latest: null, update_available: false, notes: '',
  installer_name: null, size: null, checked_at: null, check_error: null, release_lookup: 'unchecked',
  download: 'idle',
  downloaded_bytes: 0, download_error: null, verified: false, verified_version: null, verified_name: null,
  can_install: false, signature_check: false, signature_verified: false, auto_check: false, custom_source: false,
  app_update: 'none', app_update_here: true, app_size: null, app_download: 'idle', app_downloaded_bytes: 0,
  app_download_error: null, app_verified: false, app_verified_version: null, can_apply_app: false,
  app_last_result: null, app_last_result_at: null,
}
const AVAILABLE: UpdateStatus = {
  ...BASE, latest: '0.2.0', update_available: true, installer_name: 'BaiheStudio-Setup-0.2.0.exe',
  size: 104_000_000, checked_at: 1759000000, release_lookup: 'found',
}

const reply = (body: unknown, status = 200) =>
  vi.fn(async (..._args: Parameters<typeof fetch>) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  )

afterEach(() => resetPcModeForTests())

describe('updateModel', () => {
  it('names the version, or a source checkout', () => {
    expect(versionLine(BASE)).toBe('Version 0.1.0')
    expect(versionLine({ ...BASE, current: null, installed: false })).toBe('Source checkout')
  })

  it('says what the last check found', () => {
    expect(checkLine(BASE)).toBe('Not checked yet.')
    // Releases exist but none carries the installer (e.g. only frontend-v*).
    expect(checkLine({ ...BASE, checked_at: 1, release_lookup: 'no_installer_release' })).toBe(
      NO_INSTALLER_RELEASE_LINE,
    )
    // A 404: the private-releases hint, without claiming it is the cause.
    expect(checkLine({ ...BASE, checked_at: 1, release_lookup: 'not_found' })).toBe(NO_RELEASE_LINE)
    // No cause is claimed; the manual, hash-checked route is named.
    expect(NO_RELEASE_LINE).toMatch(/^No release found\. If .*by hand.*\.sha256/)
    expect(checkLine(AVAILABLE)).toBe('Version 0.2.0 is available (104.0 MB).')
    expect(checkLine({ ...AVAILABLE, update_available: false })).toBe('You have the latest version.')
    expect(checkLine({ ...AVAILABLE, current: null, update_available: false })).toBe('The latest release is 0.2.0.')
    expect(checkLine({ ...AVAILABLE, check_error: "Couldn't reach GitHub." })).toBe("Couldn't reach GitHub.")
  })

  it('says whether updates are signature-checked', () => {
    expect(signatureLine(BASE)).toBe(UNSIGNED_LINE)
    // A SHA-256-only download never reads as signature-verified.
    expect(signatureLine({ ...AVAILABLE, verified: true, signature_verified: false })).toBe(UNSIGNED_LINE)
    const signed = { ...AVAILABLE, signature_check: true }
    expect(signatureLine(signed)).toBe(SIGNATURE_REQUIRED_LINE)
    expect(signatureLine({ ...signed, verified: true, signature_verified: true })).toBe(SIGNATURE_VERIFIED_LINE)
    expect(signatureLine({ ...signed, verified: false, signature_verified: true })).toBe(SIGNATURE_REQUIRED_LINE)
  })

  it('reports the download', () => {
    expect(downloadLine(AVAILABLE)).toBeNull()
    expect(downloadLine({ ...AVAILABLE, download: 'downloading', downloaded_bytes: 52_000_000 })).toBe(
      'Downloading… 52.0 MB of 104.0 MB',
    )
    const verified = { ...AVAILABLE, download: 'verified' as const, verified: true,
      verified_version: '0.2.0', verified_name: 'BaiheStudio-Setup-0.2.0.exe' }
    expect(downloadLine(verified)).toBe('Downloaded and verified: BaiheStudio-Setup-0.2.0.exe.')
    expect(downloadLine({ ...AVAILABLE, download: 'failed', download_error: 'Mismatch.' })).toBe(
      'Mismatch. You can download it again.',
    )
  })

  it('Install names the verified version, not the last check', () => {
    const s = { ...AVAILABLE, latest: '0.3.0', verified: true, verified_version: '0.2.0' }
    expect(installTarget(s)).toBe('version 0.2.0')
  })

  it('promises no particular Windows prompt', () => {
    expect(SMARTSCREEN_NOTE).toMatch(/not code-signed/)
    expect(SMARTSCREEN_NOTE).not.toMatch(/Run anyway|More info/)
  })

  it('offers Download only for a newer version on an installed copy, once', () => {
    expect(canDownload(AVAILABLE)).toBe(true)
    expect(canDownload(BASE)).toBe(false)
    expect(canDownload({ ...AVAILABLE, installed: false })).toBe(false)
    expect(canDownload({ ...AVAILABLE, download: 'downloading' })).toBe(false)
    expect(canDownload({ ...AVAILABLE, verified: true })).toBe(false)
    expect(canDownload({ ...AVAILABLE, download: 'failed' })).toBe(true)
    expect(isDownloading({ ...AVAILABLE, download: 'downloading' })).toBe(true)
  })
})

describe('app-only updates', () => {
  const APP: UpdateStatus = { ...AVAILABLE, app_update: 'available', app_size: 41_000_000 }

  it('offers the app-only path, or says why the full installer is needed', () => {
    expect(appUpdateLine(BASE)).toBeNull()
    expect(appUpdateLine(APP)).toBe('The app can be updated on its own (41.0 MB), without the full installer.')
    expect(appUpdateLine({ ...APP, app_update: 'needs_full_installer' })).toBe(APP_FULL_INSTALLER_LINE)
    // The boot service can't replace its own folder (owner decision: leave it to the installer).
    expect(appUpdateLine({ ...APP, app_update_here: false })).toBe(APP_BOOT_SERVICE_LINE)
    expect(appUpdateLine({ ...APP, installed: false })).toBeNull()
  })

  it('offers the app download once, only where the swap can run', () => {
    expect(canDownloadApp(APP)).toBe(true)
    expect(canDownloadApp(AVAILABLE)).toBe(false)
    expect(canDownloadApp({ ...APP, app_update_here: false })).toBe(false)
    expect(canDownloadApp({ ...APP, app_download: 'downloading' })).toBe(false)
    expect(canDownloadApp({ ...APP, app_verified: true })).toBe(false)
    expect(canDownloadApp({ ...APP, app_download: 'failed' })).toBe(true)
    expect(isDownloading({ ...APP, app_download: 'downloading' })).toBe(true)
  })

  it('says how the app download went', () => {
    expect(appDownloadLine(APP)).toBeNull()
    expect(appDownloadLine({ ...APP, app_download: 'downloading', app_downloaded_bytes: 20_500_000 })).toBe(
      'Downloading the app update… 20.5 MB of 41.0 MB',
    )
    const verified = { ...APP, app_download: 'verified' as const, app_verified: true, app_verified_version: '0.2.0' }
    expect(appDownloadLine(verified)).toBe('App update 0.2.0 downloaded and verified.')
    expect(appApplyTarget(verified)).toBe('version 0.2.0')
    expect(appDownloadLine({ ...APP, app_download: 'failed', app_download_error: 'Hash mismatch.' })).toBe(
      'Hash mismatch. You can download it again.',
    )
  })

  it('sends the app calls PC-only, apply with confirm', async () => {
    const f = reply({ started: true, version: '0.2.0' }, 202)
    await applyAppUpdate(f)
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/system/update/app/apply')
    expect(JSON.parse(String(init?.body))).toEqual({ confirm: true })
    expect(new Headers(init?.headers).get('X-Baihe-Local')).toBe('1')
    const g = reply(BASE, 202)
    await downloadAppUpdate(g)
    expect(g.mock.calls[0][0]).toBe('/api/system/update/app/download')
  })
})

describe('update API', () => {
  it('sends every call PC-only, install with confirm', async () => {
    const f = reply({ launched: true, installer_name: 'x.exe' })
    await installUpdate(f)
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/system/update/install')
    expect(JSON.parse(String(init?.body))).toEqual({ confirm: true })
    expect(new Headers(init?.headers).get('X-Baihe-Local')).toBe('1')

    const g = reply(BASE)
    await setUpdateAutoCheck(true, g)
    expect(JSON.parse(String(g.mock.calls[0][1]?.body))).toEqual({ auto_check: true })
  })

  it('a 403 marks the tab as away from the PC', async () => {
    resetPcModeForTests('local')
    await expect(checkForUpdates(reply({ error: { code: 'forbidden', message: 'No.' } }, 403))).rejects.toThrow()
    expect(getPcMode()).toBe('remote')
  })
})
