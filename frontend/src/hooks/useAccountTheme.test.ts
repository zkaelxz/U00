import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as auth from '../api/auth'
import { applyAccountTheme, getThemePref, resetThemeState, setThemePref } from '../theme'
import { startAccountTheme } from './useAccountTheme'

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('startAccountTheme', () => {
  beforeEach(() => {
    resetThemeState()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("uses the account's saved theme over this browser's", async () => {
    applyAccountTheme('dark')
    const stop = startAccountTheme({ theme: 'sepia' })
    expect(getThemePref()).toBe('sepia')
    stop()
  })

  it("gives an account with no theme yet this browser's choice", async () => {
    applyAccountTheme('oled')
    const put = vi.spyOn(auth, 'putPreferences').mockResolvedValue({ theme: 'oled' })
    const stop = startAccountTheme({})
    await flush()
    expect(put).toHaveBeenCalledWith({ theme: 'oled' })
    stop()
  })

  it('saves a later choice on the account, and stops once stopped', async () => {
    const put = vi.spyOn(auth, 'putPreferences').mockResolvedValue({ theme: 'dark' })
    const stop = startAccountTheme(undefined)
    setThemePref('dark')
    expect(put).toHaveBeenCalledWith({ theme: 'dark' })
    stop()
    put.mockClear()
    setThemePref('light')
    expect(put).not.toHaveBeenCalled()
  })
})
