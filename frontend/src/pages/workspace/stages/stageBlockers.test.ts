import { describe, expect, it } from 'vitest'

import { exportBlocked, translateBlocker } from './stageBlockers'

describe('translateBlocker', () => {
  it('blocks with no lines, whatever the other flags', () => {
    expect(translateBlocker(0, 0, false, false)).toEqual({ kind: 'no-lines' })
    expect(translateBlocker(0, 0, true, true)).toEqual({ kind: 'no-lines' })
  })
  it('blocks when every line already has English', () => {
    expect(translateBlocker(412, 0, false, false)).toEqual({ kind: 'all-translated', total: 412 })
  })
  it('asks for the confirmation once Re-translate is on', () => {
    expect(translateBlocker(412, 0, true, false)).toEqual({ kind: 'confirm-force' })
    expect(translateBlocker(412, 5, true, false)).toEqual({ kind: 'confirm-force' })
  })
  it('runs when there is work', () => {
    expect(translateBlocker(412, 5, false, false)).toBeNull()
    expect(translateBlocker(412, 0, true, true)).toBeNull()
  })
})

describe('exportBlocked', () => {
  it('blocks only a loaded 0-line drama', () => {
    expect(exportBlocked(0)).toBe(true)
    expect(exportBlocked(3)).toBe(false)
    expect(exportBlocked(null)).toBe(false)
  })
})
