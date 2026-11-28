import { afterEach, describe, expect, it, vi } from 'vitest'

import { SHELL_KEY_EVENT, canChangeAddress, canPopOut, changeShellAddress, inPhoneShell, popOut, reportMediaState, saveInShell, setLiveWatching, setShellSwitch, shellNotificationsBlocked, shellSwitch } from './phoneShell'

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

describe('saving a file through the app', () => {
  it('leaves the file to the browser where the app cannot save it', () => {
    vi.stubGlobal('window', {})
    expect(saveInShell(new Blob(['a']), 'a.txt')).toBe(false)
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn() }, __baiheSaveKey: 'k' })
    expect(saveInShell(new Blob(['a']), 'a.txt')).toBe(false)
  })

  it('leaves the file to the browser in a page the app gave no key, such as a frame', () => {
    const saveFile = vi.fn(() => true)
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), saveFile } })
    expect(saveInShell(new Blob(['a']), 'a.txt')).toBe(false)
    expect(saveFile).not.toHaveBeenCalled()
  })

  it('hands the app its key, the name, type and bytes as base64', async () => {
    const saveFile = vi.fn(() => true)
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), saveFile }, __baiheSaveKey: 'k1' })
    expect(saveInShell(new Blob(['1\nhéllo'], { type: 'application/x-subrip' }), 'ep1.srt')).toBe(true)
    await vi.waitFor(() => expect(saveFile).toHaveBeenCalled())
    const [key, name, mime, base64] = saveFile.mock.calls[0] as unknown as [string, string, string, string]
    expect([key, name, mime]).toEqual(['k1', 'ep1.srt', 'application/x-subrip'])
    expect(Buffer.from(base64, 'base64').toString('utf8')).toBe('1\nhéllo')
  })

  it('keeps a file over the app\'s limit away from the app', () => {
    const saveFile = vi.fn(() => true)
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), saveFile }, __baiheSaveKey: 'k' })
    expect(saveInShell({ size: 31 * 1024 * 1024, type: '' } as Blob, 'big.epub')).toBe(false)
    expect(saveFile).not.toHaveBeenCalled()
  })
})

describe('changing the address', () => {
  it('asks the app with the page key, and not without one', () => {
    const changeAddress = vi.fn()
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), changeAddress } })
    expect(canChangeAddress()).toBe(false)
    changeShellAddress()
    expect(changeAddress).not.toHaveBeenCalled()
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn(), changeAddress }, __baiheSaveKey: 'k' })
    expect(canChangeAddress()).toBe(true)
    changeShellAddress()
    expect(changeAddress).toHaveBeenCalledWith('k')
    vi.stubGlobal('window', { BaiheShell: { get: () => true, set: vi.fn() } })
    expect(canChangeAddress()).toBe(false)
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

describe('lock-screen reports before the page key', () => {
  it('holds the latest report until the app sets the key, then sends it once', () => {
    const mediaState = vi.fn()
    const win = Object.assign(new EventTarget(), { BaiheShell: { get: () => true, set: vi.fn(), mediaState } }) as EventTarget & { __baiheSaveKey?: string }
    vi.stubGlobal('window', win)
    const state = { position: 3, duration: 100, rate: 1, title: 'Moonlit Hall 3', artist: 'Example Studio' }
    const now = vi.spyOn(Date, 'now').mockReturnValue(1000)
    reportMediaState({ ...state, state: 'paused' })
    reportMediaState({ ...state, state: 'playing', rate: 1.5 })
    expect(mediaState).not.toHaveBeenCalled()
    now.mockReturnValue(9000)
    win.__baiheSaveKey = 'k'
    win.dispatchEvent(new Event(SHELL_KEY_EVENT))
    win.dispatchEvent(new Event(SHELL_KEY_EVENT))
    expect(mediaState).toHaveBeenCalledTimes(1)
    expect(mediaState.mock.calls[0][0]).toBe('k')
    // Moved on by the 8 s waited at 1.5x, so the lock screen's clock is not behind the sound.
    expect(JSON.parse(mediaState.mock.calls[0][1] as string)).toMatchObject({ state: 'playing', position: 15, at: 9000 })
    now.mockRestore()
  })
})
