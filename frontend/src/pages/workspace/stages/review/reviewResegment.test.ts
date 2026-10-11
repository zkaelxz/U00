import { describe, expect, it } from 'vitest'

import { QUICK_SPLIT_DEFAULTS, quickSplitHelp, quickSplitLimits } from './reviewResegment'

describe('quickSplitLimits', () => {
  it('keeps 12 seconds and 40 characters until Settings says otherwise', () => {
    expect(QUICK_SPLIT_DEFAULTS).toEqual({ seconds: 12, chars: 40 })
    expect(quickSplitLimits(null)).toEqual({ seconds: 12, chars: 40 })
    expect(quickSplitLimits({})).toEqual({ seconds: 12, chars: 40 })
  })

  it('takes the saved numbers', () => {
    expect(quickSplitLimits({ quick_split_seconds: 9, split_max_cjk_chars: 30 })).toEqual({ seconds: 9, chars: 30 })
  })

  it('ignores a value that is not a usable number', () => {
    expect(quickSplitLimits({ quick_split_seconds: Number.NaN, split_max_cjk_chars: 0 })).toEqual({ seconds: 12, chars: 40 })
  })

  it('words the button help with the limits in force', () => {
    expect(quickSplitHelp({ seconds: 9, chars: 30 })).toContain('over about 9 seconds or 30 characters')
  })
})
