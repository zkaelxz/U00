import { describe, expect, it } from 'vitest'
import { fieldIds, helpOpenAfterClick } from './fieldIds'

describe('fieldIds', () => {
  it('has no describedBy without help or error', () => {
    expect(fieldIds('f', {}).describedBy).toBeUndefined()
  })
  it('lists help then error', () => {
    expect(fieldIds('f', { help: true }).describedBy).toBe('f-help')
    expect(fieldIds('f', { error: true }).describedBy).toBe('f-error')
    expect(fieldIds('f', { help: true, error: true }).describedBy).toBe('f-help f-error')
  })
  it('uses the base as control id', () => expect(fieldIds('f', {}).controlId).toBe('f'))
})

describe('helpOpenAfterClick', () => {
  it('keeps a tap-opened help open: the press found it closed', () => {
    expect(helpOpenAfterClick(false, true)).toBe(true)
  })
  it('closes on a press that found it open', () => {
    expect(helpOpenAfterClick(true, true)).toBe(false)
  })
  it('toggles the current state when there was no press (keyboard)', () => {
    expect(helpOpenAfterClick(null, true)).toBe(false)
    expect(helpOpenAfterClick(null, false)).toBe(true)
  })
})
