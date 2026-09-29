import { describe, expect, it } from 'vitest'
import { fieldIds } from './fieldIds'

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
