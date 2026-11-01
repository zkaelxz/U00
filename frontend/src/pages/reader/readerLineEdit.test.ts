import { describe, expect, it } from 'vitest'

import { lineEditPatch } from './readerLineEdit'

describe('lineEditPatch', () => {
  it('sends nothing when the text is unchanged', () => {
    expect(lineEditPatch('Hello', 'Hello')).toBeNull()
  })

  it('sends the new text with the text that was seen, so a stale save is refused', () => {
    expect(lineEditPatch('Hello', 'Hi')).toEqual({ en: 'Hi', expected: { en: 'Hello' } })
  })

  it('lets a line be emptied, as Review does', () => {
    expect(lineEditPatch('Hello', '')).toEqual({ en: '', expected: { en: 'Hello' } })
  })
})
