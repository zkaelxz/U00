import { describe, expect, it } from 'vitest'

import { speakerOptions } from './speakerOptions'

describe('speakerOptions', () => {
  it('lists the title speakers as they are', () => {
    expect(speakerOptions(['Anok', 'Uririn'], 'Anok')).toEqual(['Anok', 'Uririn'])
  })
  it('keeps a value no line carries so the draft is not changed', () => {
    expect(speakerOptions(['Anok'], 'Mei')).toEqual(['Anok', 'Mei'])
  })
  it('adds nothing for an empty value', () => {
    expect(speakerOptions(['Anok'], '')).toEqual(['Anok'])
  })
})
