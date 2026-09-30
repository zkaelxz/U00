import { afterEach, describe, expect, it, vi } from 'vitest'

import { getPcMode, resetPcModeForTests } from '../../api/pcOnly'
import { checkForUpdates, installUpdate, setUpdateAutoCheck } from '../../api/update'
import type { UpdateStatus } from '../../types/update'
import {
  NO_INSTALLER_RELEASE_LINE, NO_RELEASE_LINE, SMARTSCREEN_NOTE, canDownload, checkLine, downloadLine, installTarget, isDownloading,
  versionLine,
} from './updateModel'

const BASE: UpdateStatus = {
  current: '0.1.0', installed: true, latest: null, update_available: false, notes: '',
  installer_name: null, size: null, checked_at: null, check_error: null, release_lookup: 'unchecked',
  download: 'idle',
  downloaded_bytes: 0, download_error: null, verified: false, verified_version: null, verified_name: null,
  can_install: false, auto_check: false, custom_source: false,
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
