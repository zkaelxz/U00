import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  backUpNow, deleteSnapshot, getBackupSettings, getSnapshot, getSnapshotDramas, restoreSnapshotDrama,
  updateBackupSettings,
} from './backups'
import { ApiError } from './client'
import { getPcMode, resetPcModeForTests } from './pcOnly'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}

const header = (init: RequestInit, name: string) => new Headers(init.headers).get(name)

afterEach(() => {
  resetPcModeForTests()
  vi.unstubAllGlobals()
})

describe('backups api', () => {
  it('reads go to the right URLs as GETs with the PC header', async () => {
    const { mock, f } = reply(200, {})
    await getBackupSettings(f)
    await getSnapshot(f)
    await getSnapshotDramas(f)
    expect(mock.mock.calls.map(([u]) => u)).toEqual([
      '/api/backups/settings', '/api/backups/snapshot', '/api/backups/snapshot/dramas',
    ])
    for (const [, init] of mock.mock.calls) {
      expect(init.method ?? 'GET').toBe('GET')
      expect(header(init, 'X-Baihe-Local')).toBe('1')
    }
  })

  it('settings sends only the changed fields', async () => {
    const { mock, f } = reply(200, {})
    await updateBackupSettings({ frequency: 'daily' }, f)
    await updateBackupSettings({ folder: '' }, f)
    const [[url, init], [, init2]] = mock.mock.calls
    expect(url).toBe('/api/backups/settings')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ frequency: 'daily' })
    expect(JSON.parse(init2.body)).toEqual({ folder: '' })
  })

  it('back up now, restore and delete send the confirm fields', async () => {
    const { mock, f } = reply(200, {})
    await backUpNow(false, f)
    await backUpNow(true, f)
    await restoreSnapshotDrama(7, f)
    await deleteSnapshot(f)
    expect(mock.mock.calls.map(([u]) => u)).toEqual([
      '/api/backups/now', '/api/backups/now', '/api/backups/snapshot/restore-drama', '/api/backups/snapshot/delete',
    ])
    expect(mock.mock.calls.map(([, i]) => JSON.parse(i.body))).toEqual([
      { replace: false },
      { replace: true },
      { drama_id: 7, confirm: true, confirm_text: 'RESTORE' },
      { confirm: true, confirm_text: 'DELETE' },
    ])
  })

  it('writes carry the CSRF token when signed in; reads do not', async () => {
    vi.stubGlobal('document', { cookie: 'baihe_csrf=tok' })
    const { mock, f } = reply(200, {})
    await updateBackupSettings({ enabled: true }, f)
    await getBackupSettings(f)
    const [[, post], [, get]] = mock.mock.calls
    expect(header(post, 'X-CSRF-Token')).toBe('tok')
    expect(header(post, 'X-Baihe-Local')).toBe('1')
    expect(header(get, 'X-CSRF-Token')).toBeNull()
  })

  it('a 403 from away from the PC marks the tab remote and throws', async () => {
    const { f } = reply(403, { error: { code: 'forbidden', message: 'PC only.' } })
    await expect(getBackupSettings(f)).rejects.toBeInstanceOf(ApiError)
    expect(getPcMode()).toBe('remote')
  })

  it('a 422 keeps the server message for the caller', async () => {
    const { f } = reply(422, { error: { code: 'invalid_input', message: "That backup folder doesn't exist. Create it first." } })
    await expect(updateBackupSettings({ folder: 'x' }, f)).rejects.toMatchObject({
      status: 422, code: 'invalid_input', message: "That backup folder doesn't exist. Create it first.",
    })
    expect(getPcMode()).toBe('unknown')
  })
})
