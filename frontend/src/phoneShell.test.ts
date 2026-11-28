import { afterEach, describe, expect, it, vi } from 'vitest'

import { canPopOut, inPhoneShell, popOut, setLiveWatching, setShellSwitch, shellNotificationsBlocked, shellSwitch } from './phoneShell'

afterEach(() => { vi.unstubAllGlobals() })

describe('phone shell switches', () => {
  it('is absent in an ordinary browser', () => {
    vi.stubGlobal('window', {})
    expect(inPhoneShell()).toBe(false)
    expect(shellSwitch('alerts_on')).toBe(false)
    expect(() => setShellSwitch('alerts_on', true)).not.toThrow()
  })

  it('reads and sets the app\'s switches through the bridge', () => {
    const set = vi.fn()
    vi.stubGlobal('window', { BaiheShell: { get: (n: string) => n === 'alerts_on', set } })
    expect(inPhoneShell()).toBe(true)
    expect(shellSwitch('alerts_on')).toBe(true)
    expect(shellSwitch('clipboard_offer_on')).toBe(false)
    setShellSwitch('alerts_on', false)
    expect(set).toHaveBeenCalledWith('alerts_on', false)
  })

  it('floats the picture only through an app build that can', () => {
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn() } })
    expect(canPopOut()).toBe(false)
    expect(() => { setLiveWatching(true); popOut() }).not.toThrow()
    const setWatching = vi.fn()
    const pop = vi.fn()
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), setLiveWatching: setWatching, popOut: pop } })
    expect(canPopOut()).toBe(true)
    setLiveWatching(true)
    popOut()
    expect(setWatching).toHaveBeenCalledWith(true)
    expect(pop).toHaveBeenCalled()
  })
})

describe('notifications blocked', () => {
  it('asks the app, and says not blocked where nothing can tell', () => {
    vi.stubGlobal('window', {})
    expect(shellNotificationsBlocked()).toBe(false)
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn() } })
    expect(shellNotificationsBlocked()).toBe(false)
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), notificationsBlocked: () => true } })
    expect(shellNotificationsBlocked()).toBe(true)
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), notificationsBlocked: () => { throw new Error('gone') } } })
    expect(shellNotificationsBlocked()).toBe(false)
  })
})

describe('check for updates', () => {
  it('calls the app when it has the button and is quiet in a browser', async () => {
    const check = vi.fn()
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), checkForUpdates: check } })
    const m = await import('./phoneShell')
    expect(m.canCheckForUpdates()).toBe(true)
    m.checkForUpdates()
    expect(check).toHaveBeenCalledTimes(1)
    vi.stubGlobal('window', {})
    expect(m.canCheckForUpdates()).toBe(false)
    expect(() => m.checkForUpdates()).not.toThrow()
  })
})

describe('hiding the app strip', () => {
  it('asks the app to hide and show its strip, and is quiet without it', async () => {
    const hide = vi.fn()
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), setBarsHidden: hide } })
    const m = await import('./phoneShell')
    m.setShellBarsHidden(true)
    m.setShellBarsHidden(false)
    expect(hide.mock.calls).toEqual([[true], [false]])
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn() } })
    expect(() => m.setShellBarsHidden(true)).not.toThrow()
  })
})

describe('problem report diagnostics', () => {
  it('reads the build line and crash text, and is null without the call or with bad data', async () => {
    const m = await import('./phoneShell')
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), diagnostics: () => '{"phone":"Build 12","crash":"boom"}' } })
    expect(m.shellDiagnostics()).toEqual({ phone: 'Build 12', crash: 'boom' })
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), diagnostics: () => 'not json' } })
    expect(m.shellDiagnostics()).toBeNull()
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn() } })
    expect(m.shellDiagnostics()).toBeNull()
  })
})
