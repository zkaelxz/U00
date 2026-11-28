import { afterEach, describe, expect, it, vi } from 'vitest'

import { canPopOut, inPhoneShell, popOut, saveInShell, setLiveWatching, setShellSwitch, shellSwitch } from './phoneShell'

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
