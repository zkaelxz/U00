import { afterEach, describe, expect, it, vi } from 'vitest'

import { isPopOutName, openInNewWindow, popOutName, POP_OUT_PREFIX } from './popOut'

describe('pop-out window name', () => {
  it('recognises the names this app gives its windows and nothing else', () => {
    expect(isPopOutName(popOutName(1))).toBe(true)
    expect(isPopOutName(`${POP_OUT_PREFIX}x`)).toBe(true)
    expect(isPopOutName('')).toBe(false)
    expect(isPopOutName('some-other-window')).toBe(false)
  })

  it('gives each window its own name', () => {
    expect(popOutName(1)).not.toBe(popOutName(2))
  })
})

describe('openInNewWindow', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('opens the same hash address under a pop-out name', () => {
    const open = vi.fn()
    vi.stubGlobal('window', { open, location: { href: 'http://127.0.0.1:8600/#/drama/3/review' } })
    openInNewWindow('#/drama/3/review?tools=coverage')
    const [url, name] = open.mock.calls[0]
    expect(url).toBe('http://127.0.0.1:8600/#/drama/3/review?tools=coverage')
    expect(isPopOutName(name)).toBe(true)
  })
})
