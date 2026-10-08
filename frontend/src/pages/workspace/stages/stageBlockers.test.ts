import { describe, expect, it } from 'vitest'

import { ApiError } from '../../../api/client'
import { exportBlocked, needsReplaceConfirm, translateBlocker } from './stageBlockers'

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

describe('needsReplaceConfirm', () => {
  it('is true only for the 422 that asks to confirm replacing media', () => {
    const err = (status: number, reason?: string) =>
      new ApiError(status, { code: 'x', message: 'm', details: reason ? { reason } : undefined })
    expect(needsReplaceConfirm(err(422, 'confirm_replace_audio'))).toBe(true)
    expect(needsReplaceConfirm(err(422, 'other'))).toBe(false)
    expect(needsReplaceConfirm(err(422))).toBe(false)
    expect(needsReplaceConfirm(err(409, 'confirm_replace_audio'))).toBe(false)
    expect(needsReplaceConfirm(new Error('x'))).toBe(false)
  })
})
