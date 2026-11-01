import { describe, expect, it } from 'vitest'

import { noneAsNull } from './glossaryTermForm'

describe('noneAsNull', () => {
  it('turns the "None" option into null so the API accepts it', () => {
    expect(noneAsNull('')).toBeNull()
  })
  it('keeps a chosen value', () => {
    expect(noneAsNull('name')).toBe('name')
  })
})
