import { describe, expect, it } from 'vitest'

import { changedCards } from './settingsChanges'

describe('changedCards', () => {
  it('flags nothing on a first visit', () => {
    expect(changedCards(['costs/spending'], null)).toEqual({})
  })
  it('flags a card the browser has not seen as new', () => {
    expect(changedCards(['costs/spending', 'costs/budget'], ['costs/spending'])).toEqual({ budget: 'new' })
  })
  it('flags a card seen under another tab as moved', () => {
    expect(changedCards(['account/devices'], ['advanced/devices'])).toEqual({ devices: 'moved' })
  })
  it('flags nothing when every card was seen', () => {
    expect(changedCards(['costs/spending'], ['costs/spending', 'old/gone'])).toEqual({})
  })
})
