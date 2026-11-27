import { describe, expect, it } from 'vitest'

import { speakerOptions } from './speakerOptions'

describe('speakerOptions', () => {
  it('lists the title speakers as they are', () => {
    expect(speakerOptions(['Maple', 'Cedar'], 'Maple')).toEqual(['Maple', 'Cedar'])
  })
  it('keeps a value no line carries so the draft is not changed', () => {
    expect(speakerOptions(['Maple'], 'Mei')).toEqual(['Maple', 'Mei'])
  })
  it('adds nothing for an empty value', () => {
    expect(speakerOptions(['Maple'], '')).toEqual(['Maple'])
  })
})
