import { describe, expect, it } from 'vitest'

import { prefillPrompt } from './transcribePrompt'

describe('prefillPrompt', () => {
  it('fills an empty box with the automatic prompt', () => {
    expect(prefillPrompt('', '苏杉。他走了。')).toBe('苏杉。他走了。')
  })
  it('fills a whitespace-only box', () => {
    expect(prefillPrompt('  ', '苏杉。')).toBe('苏杉。')
  })
  it('keeps a prompt the user typed and saved', () => {
    expect(prefillPrompt('沈清疑', '苏杉。')).toBe('沈清疑')
  })
  it('stays empty when there is no automatic prompt', () => {
    expect(prefillPrompt('', '')).toBe('')
    expect(prefillPrompt('', undefined)).toBe('')
  })
})
