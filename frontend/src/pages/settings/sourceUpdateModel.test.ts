import { describe, expect, it } from 'vitest'

import type { SourceUpdateStatus } from '../../types/update'
import { sourceCheckLine, sourceReason, sourceVersionLine } from './sourceUpdateModel'

const base: SourceUpdateStatus = {
  available: true, current: 'abcdef0123', checked: false, behind: null, latest: null, changes: [], reason: null,
  can_apply: false, applying: false, last_result: null, last_result_at: null,
}

describe('sourceCheckLine', () => {
  it('says what the check found', () => {
    expect(sourceCheckLine(base)).toBe('Not checked yet.')
    expect(sourceCheckLine({ ...base, checked: true })).toBe("Couldn't check.")
    expect(sourceCheckLine({ ...base, checked: true, behind: 0 })).toBe('Up to date.')
    expect(sourceCheckLine({ ...base, checked: true, behind: 1 })).toBe('1 update waiting.')
    expect(sourceCheckLine({ ...base, checked: true, behind: 3 })).toBe('3 updates waiting.')
  })
})

describe('sourceReason', () => {
  it('shows the server reason unless the copy is up to date', () => {
    expect(sourceReason({ ...base, checked: true, behind: null, reason: "Couldn't reach GitHub. Try again later." }))
      .toBe("Couldn't reach GitHub. Try again later.")
    expect(sourceReason({ ...base, checked: true, behind: 0, reason: 'x' })).toBeNull()
    expect(sourceReason({ ...base, checked: true, behind: 2 })).toBeNull()
  })
})

describe('sourceVersionLine', () => {
  it('names the commit', () => {
    expect(sourceVersionLine(base)).toBe('Version abcdef0123')
    expect(sourceVersionLine({ ...base, current: null })).toBeUndefined()
  })
})
