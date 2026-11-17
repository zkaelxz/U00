import { describe, expect, it } from 'vitest'

import { QUICK_SPLIT_DEFAULTS, quickSplitHelp, quickSplitLimits, resplitCutRows } from './reviewResegment'

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

describe('resplitCutRows', () => {
  const cut = (id: number, idx: number) => ({
    line_id: id, idx,
    pieces: [{ start: 2, end: 9.4, text: '你好。' }, { start: 9.45, end: 20, text: '再见。' }, { start: 20, end: 32, text: '走了。' }],
  })

  it('names each split line by its shown number, where it is cut, and its pieces', () => {
    expect(resplitCutRows([cut(7, 4)])).toEqual({
      rows: [{ key: 7, number: 5, at: '0:09.4, 0:20.0', pieces: '你好。 | 再见。 | 走了。' }],
      more: 0,
    })
  })

  it('lists the first 20 and counts the rest', () => {
    const r = resplitCutRows(Array.from({ length: 23 }, (_, i) => cut(i + 1, i)))
    expect(r.rows).toHaveLength(20)
    expect(r.more).toBe(3)
  })

  it('has no rows when the result carries no cuts', () => {
    expect(resplitCutRows(undefined)).toEqual({ rows: [], more: 0 })
  })
})
