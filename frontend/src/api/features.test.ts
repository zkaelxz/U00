import { afterEach, describe, expect, it } from 'vitest'

import { applyFeatures, featureOn, getFeatures } from './features'
import { applyMeta, resetPcModeForTests } from './pcOnly'

afterEach(() => {
  applyFeatures(undefined)
  resetPcModeForTests()
})

describe('feature switches from /api/meta', () => {
  it('reads every area as on until the server says off', () => {
    expect(featureOn('jellyfin')).toBe(true)
    applyFeatures({ jellyfin: false, sources: true })
    expect(featureOn('jellyfin')).toBe(false)
    expect(featureOn('sources')).toBe(true)
    expect(featureOn('extension')).toBe(true)
  })

  it('ignores answers that are not booleans', () => {
    applyFeatures({ jellyfin: 'no', sources: 0, extension: null })
    expect(getFeatures()).toEqual({})
    applyFeatures('off')
    expect(featureOn('jellyfin')).toBe(true)
  })

  it('is applied together with the PC mode', () => {
    applyMeta({ local: true, features: { extension: false } })
    expect(featureOn('extension')).toBe(false)
  })

  it('goes back to on when a later answer carries no switches', () => {
    applyFeatures({ sources: false })
    applyMeta({ local: true })
    expect(featureOn('sources')).toBe(true)
  })
})
