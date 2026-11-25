import { afterEach, describe, expect, it, vi } from 'vitest'

import { inPhoneShell, setShellSwitch, shellSwitch } from './phoneShell'

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
})
