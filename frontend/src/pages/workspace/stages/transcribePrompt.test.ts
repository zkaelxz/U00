import { describe, expect, it } from 'vitest'

import { promptFields } from './transcribePrompt'

describe('promptFields', () => {
  it('sends nothing when both are empty, so the server uses the automatic prompt', () => {
    expect(promptFields('', '')).toEqual({})
    expect(promptFields('  ', ' ')).toEqual({})
  })
  it('sends the extra names, trimmed', () => {
    expect(promptFields('', ' 沈清疑、云隐宗 ')).toEqual({ extra_names: '沈清疑、云隐宗' })
  })
  it('sends only the override when one is typed', () => {
    expect(promptFields('全部替换', '沈清疑')).toEqual({ initial_prompt: '全部替换' })
  })
})
