import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { PcMode } from '../../api/pcOnly'
import { announceDeveloperMode, loadDeveloperMode, type DeveloperModeDeps } from './developerMode'

function deps(mode: PcMode, settings: () => Promise<{ developer_mode: boolean }>): DeveloperModeDeps & { getSettings: ReturnType<typeof vi.fn> } {
  return { waitForPcMode: () => Promise.resolve(), pcMode: () => mode, getSettings: vi.fn(settings) }
}

describe('loadDeveloperMode (Assistant nav link)', () => {
  it('is hidden when Developer Mode is off', async () => {
    expect(await loadDeveloperMode(deps('local', async () => ({ developer_mode: false })))).toBe(false)
  })

  it('is shown when Developer Mode is on', async () => {
    expect(await loadDeveloperMode(deps('local', async () => ({ developer_mode: true })))).toBe(true)
    // /api/meta failed or had no `local`: still asks the server, which enforces.
    expect(await loadDeveloperMode(deps('unknown', async () => ({ developer_mode: true })))).toBe(true)
  })

  it('is hidden on a 403 (not the PC) or any other error', async () => {
    const forbidden = new ApiError(403, { code: 'forbidden', message: 'PC only.' })
    expect(await loadDeveloperMode(deps('local', () => Promise.reject(forbidden)))).toBe(false)
    expect(await loadDeveloperMode(deps('local', () => Promise.reject(new Error('boom'))))).toBe(false)
    // A missing or odd body is not "on".
    expect(await loadDeveloperMode(deps('local', async () => null as unknown as { developer_mode: boolean }))).toBe(false)
    expect(await loadDeveloperMode(deps('local', async () => ({ developer_mode: 'yes' as unknown as boolean })))).toBe(false)
  })

  it('makes no call from another device', async () => {
    const d = deps('remote', async () => ({ developer_mode: true }))
    expect(await loadDeveloperMode(d)).toBe(false)
    expect(d.getSettings).not.toHaveBeenCalled()
  })

  it('is hidden when the PC check itself fails', async () => {
    const d = { ...deps('local', async () => ({ developer_mode: true })), waitForPcMode: () => Promise.reject(new Error('x')) }
    expect(await loadDeveloperMode(d)).toBe(false)
  })
})

describe('announceDeveloperMode', () => {
  it('tells the nav to ask the server again (Settings is now the only switch)', () => {
    // The suite runs in node: a bare EventTarget stands in for window.
    const target = new EventTarget()
    vi.stubGlobal('window', target)
    const seen = vi.fn()
    target.addEventListener('baihe:developer-mode', seen)
    announceDeveloperMode(true)
    vi.unstubAllGlobals()
    expect(seen).toHaveBeenCalledTimes(1)
    expect((seen.mock.calls[0][0] as CustomEvent).detail).toEqual({ on: true })
  })
})
