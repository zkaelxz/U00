import { afterEach, describe, expect, it, vi } from 'vitest'

import { attachLockScreen, lockScreenAction } from './useLockScreen'

class FakeMedia extends EventTarget {
  paused = true
  ended = false
  currentTime = 0
  duration = 100
  playbackRate = 1
  play = vi.fn(() => {
    this.paused = false
    this.dispatchEvent(new Event('play'))
    return Promise.resolve()
  })
  pause = vi.fn(() => {
    this.paused = true
    this.dispatchEvent(new Event('pause'))
  })
}

const media = () => new FakeMedia()
const asEl = (m: FakeMedia) => m as unknown as HTMLMediaElement
const meta = () => ({ title: 'Moonlit Hall 3', artist: 'Example Studio' })
type ShellWin = { BaiheShell?: unknown; __baiheMedia?: (action: string, value?: number) => void }

function inShell() {
  const mediaState = vi.fn()
  const win: ShellWin = { BaiheShell: { get: () => false, set: vi.fn(), mediaState } }
  vi.stubGlobal('window', win)
  vi.stubGlobal('navigator', {})
  const reports = () => mediaState.mock.calls.map(([json]) => JSON.parse(json as string))
  return { win, reports }
}

afterEach(() => { vi.unstubAllGlobals() })

describe('lock-screen controls in the phone app', () => {
  it('reports play, pause and the end, and stops the controls on leaving', () => {
    const { reports } = inShell()
    const m = media()
    const controls = attachLockScreen(asEl(m), meta)
    expect(reports()).toEqual([])
    m.currentTime = 12.5
    m.playbackRate = 1.5
    void m.play()
    expect(reports().at(-1)).toMatchObject({ state: 'playing', position: 12.5, duration: 100, rate: 1.5, title: 'Moonlit Hall 3', artist: 'Example Studio' })
    expect(typeof reports().at(-1).at).toBe('number')
    m.pause()
    expect(reports().at(-1).state).toBe('paused')
    m.ended = true
    m.dispatchEvent(new Event('ended'))
    expect(reports().at(-1).state).toBe('stopped')
    m.ended = false
    void m.play()
    const before = reports().length
    controls.detach()
    expect(reports().length).toBe(before + 1)
    expect(reports().at(-1).state).toBe('stopped')
    m.pause()
    expect(reports().length).toBe(before + 1)
  })

  it('sends an unknown duration as null and a new title again', () => {
    const { reports } = inShell()
    const m = media()
    m.duration = Infinity
    let title = 'Loading'
    const controls = attachLockScreen(asEl(m), () => ({ title, artist: '' }))
    void m.play()
    expect(reports().at(-1).duration).toBeNull()
    title = 'Moonlit Hall 3'
    controls.refresh()
    expect(reports().at(-1)).toMatchObject({ state: 'playing', title: 'Moonlit Hall 3' })
  })

  it('takes the presses the app hands back until it is detached', () => {
    const { win } = inShell()
    const m = media()
    m.currentTime = 50
    const controls = attachLockScreen(asEl(m), meta)
    win.__baiheMedia?.('play', 0)
    expect(m.play).toHaveBeenCalledTimes(1)
    win.__baiheMedia?.('forward', 10)
    expect(m.currentTime).toBe(60)
    win.__baiheMedia?.('back', 10)
    expect(m.currentTime).toBe(50)
    win.__baiheMedia?.('seek', 42)
    expect(m.currentTime).toBe(42)
    win.__baiheMedia?.('pause', 0)
    expect(m.pause).toHaveBeenCalledTimes(1)
    controls.detach()
    expect(win.__baiheMedia).toBeUndefined()
  })

  it('leaves a newer page\'s handler in place when an older one detaches', () => {
    const { win } = inShell()
    const first = attachLockScreen(asEl(media()), meta)
    const second = attachLockScreen(asEl(media()), meta)
    const handler = win.__baiheMedia
    first.detach()
    expect(win.__baiheMedia).toBe(handler)
    second.detach()
    expect(win.__baiheMedia).toBeUndefined()
  })

  it('is quiet in a browser and in an app build without the call', () => {
    vi.stubGlobal('window', {})
    vi.stubGlobal('navigator', {})
    const m = media()
    expect(() => {
      const c = attachLockScreen(asEl(m), meta)
      void m.play()
      c.detach()
    }).not.toThrow()
    vi.stubGlobal('window', { BaiheShell: { get: () => false, set: vi.fn() } })
    expect(() => {
      const c = attachLockScreen(asEl(m), meta)
      m.pause()
      c.detach()
    }).not.toThrow()
  })
})

describe('presses on the player', () => {
  it('keeps a skip inside the sound', () => {
    const m = media()
    m.currentTime = 4
    lockScreenAction(asEl(m), 'back', 10)
    expect(m.currentTime).toBe(0)
    m.currentTime = 95
    lockScreenAction(asEl(m), 'forward')
    expect(m.currentTime).toBe(100)
    lockScreenAction(asEl(m), 'seek', Number.NaN)
    lockScreenAction(asEl(m), 'rewind', 3)
    expect(m.currentTime).toBe(100)
  })

  it('survives a play the browser refuses', async () => {
    const m = media()
    m.play.mockImplementationOnce(() => Promise.reject(new Error('NotAllowedError')))
    expect(() => lockScreenAction(asEl(m), 'play')).not.toThrow()
    await Promise.resolve()
  })
})

describe('a phone browser\'s own media controls', () => {
  it('names the title, follows the player and clears itself on leaving', () => {
    vi.stubGlobal('window', {})
    const handlers = new Map<string, unknown>()
    const session = {
      metadata: null as unknown,
      playbackState: 'none',
      setActionHandler: vi.fn((name: string, h: unknown) => {
        if (name === 'stop') throw new Error('not supported')
        handlers.set(name, h)
      }),
      setPositionState: vi.fn(),
    }
    vi.stubGlobal('navigator', { mediaSession: session })
    vi.stubGlobal('MediaMetadata', class {
      init: unknown
      constructor(init: unknown) { this.init = init }
    })
    const m = media()
    m.currentTime = 30
    const controls = attachLockScreen(asEl(m), () => ({ ...meta(), artwork: '/api/dramas/3/cover' }))
    expect((session.metadata as { init: unknown }).init).toEqual({ title: 'Moonlit Hall 3', artist: 'Example Studio', artwork: [{ src: '/api/dramas/3/cover' }] })
    void m.play()
    expect(session.playbackState).toBe('playing')
    expect(session.setPositionState).toHaveBeenLastCalledWith({ duration: 100, playbackRate: 1, position: 30 })
    ;(handlers.get('seekforward') as (d: { seekOffset?: number }) => void)({})
    expect(m.currentTime).toBe(40)
    ;(handlers.get('seekto') as (d: { seekTime?: number }) => void)({ seekTime: 70 })
    expect(m.currentTime).toBe(70)
    controls.detach()
    expect(session.metadata).toBeNull()
    expect(session.playbackState).toBe('none')
    expect(handlers.get('play')).toBeNull()
  })
})
