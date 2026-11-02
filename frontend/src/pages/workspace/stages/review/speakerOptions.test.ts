import { describe, expect, it } from 'vitest'

import { speakerOptions } from './speakerOptions'

describe('speakerOptions', () => {
  it('lists the title speakers as they are', () => {
    expect(speakerOptions(['Anvil', 'Urchin'], 'Anvil')).toEqual(['Anvil', 'Urchin'])
  })
  it('keeps a value no line carries so the draft is not changed', () => {
    expect(speakerOptions(['Anvil'], 'Mei')).toEqual(['Anvil', 'Mei'])
  })
  it('adds nothing for an empty value', () => {
    expect(speakerOptions(['Anvil'], '')).toEqual(['Anvil'])
  })
})
