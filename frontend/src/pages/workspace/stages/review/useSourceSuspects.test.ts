import { describe, expect, it } from 'vitest'

import { misheardScanDelay } from './useSourceSuspects'

const at = (reloads: number, dramaId = 1) => ({ dramaId, reloads })

describe('misheardScanDelay', () => {
  it('scans a title at once the first time, and again for another title', () => {
    expect(misheardScanDelay(null, at(0), false, false)).toBe(0)
    expect(misheardScanDelay(at(3), at(0, 2), false, false)).toBe(0)
  })

  it('leaves a closed panel alone after line edits', () => {
    expect(misheardScanDelay(at(0), at(5), false, false)).toBeNull()
  })

  it('waits for a pause in the edits while the panel is open, and scans at once when it opens on a stale result', () => {
    expect(misheardScanDelay(at(0), at(5), true, false)).toBeGreaterThan(0)
    expect(misheardScanDelay(at(0), at(5), true, true)).toBe(0)
  })

  it('does not scan again when nothing changed', () => {
    expect(misheardScanDelay(at(5), at(5), true, true)).toBeNull()
  })
})
