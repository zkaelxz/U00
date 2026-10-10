import { describe, expect, it } from 'vitest'

import { showGetStarted } from './getStartedLogic'

describe('showGetStarted', () => {
  it('shows only for a loaded empty library that was not dismissed', () => {
    expect(showGetStarted(0, false)).toBe(true)
    expect(showGetStarted(0, true)).toBe(false)
    expect(showGetStarted(1, false)).toBe(false)
    expect(showGetStarted(null, false)).toBe(false)
    expect(showGetStarted(undefined, false)).toBe(false)
  })
})
