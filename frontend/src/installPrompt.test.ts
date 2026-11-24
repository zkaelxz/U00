import { describe, expect, it } from 'vitest'

import { suppressInstallPrompt } from './installPrompt'

describe('suppressInstallPrompt', () => {
  it('cancels the browser install pop-up', () => {
    const target = new EventTarget()
    suppressInstallPrompt(target as unknown as Window)
    const ev = new Event('beforeinstallprompt', { cancelable: true })
    target.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(true)
  })
})
