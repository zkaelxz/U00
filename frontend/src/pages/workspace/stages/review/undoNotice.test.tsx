import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { UndoNotice } from './UndoNotice'
import { bulkControlName, isUndoKey, splitAnnouncement } from './reviewLogic'
import { caretToEnd } from './caretToEnd'

describe('UndoNotice', () => {
  const html = renderToStaticMarkup(<UndoNotice message="Split #3 into #3–#4." busy={false} onUndo={() => {}} onDismiss={() => {}} />)

  it('is a status region with a real Undo button and the Records fallback', () => {
    expect(html).toContain('role="status"')
    expect(html).toContain('<button type="button"')
    expect(html).toMatch(/>Undo<\/button>/)
    expect(html).toContain('Split #3 into #3–#4.')
    expect(html).toContain('Records → Line history')
  })
  it('has a labelled Dismiss and disables Undo while busy', () => {
    expect(html).toContain('aria-label="Dismiss"')
    const busy = renderToStaticMarkup(<UndoNotice message="m" busy onUndo={() => {}} onDismiss={() => {}} />)
    expect(busy).toMatch(/<button[^>]*disabled[^>]*>Undo/)
  })
})

describe('UndoNotice without a line-history copy', () => {
  it('leaves out the Records fallback', () => {
    const html = renderToStaticMarkup(<UndoNotice message="Line #3 is now Anvil." busy={false} historyNote={false} onUndo={() => {}} onDismiss={() => {}} />)
    expect(html).toContain('Line #3 is now Anvil.')
    expect(html).not.toContain('Records → Line history')
  })
})

describe('Ctrl+Z', () => {
  const key = (over: object) => ({ key: 'z', ctrlKey: true, metaKey: false, altKey: false, shiftKey: false, ...over })
  it('undoes on Ctrl+Z and Cmd+Z outside a text field', () => {
    expect(isUndoKey(key({}), false)).toBe(true)
    expect(isUndoKey(key({ ctrlKey: false, metaKey: true, key: 'Z' }), false)).toBe(true)
  })
  it('leaves typing, redo, other keys and IME composition alone', () => {
    expect(isUndoKey(key({}), true)).toBe(false)
    expect(isUndoKey(key({ shiftKey: true }), false)).toBe(false)
    expect(isUndoKey(key({ ctrlKey: false }), false)).toBe(false)
    expect(isUndoKey(key({ key: 'y' }), false)).toBe(false)
    expect(isUndoKey(key({ isComposing: true }), false)).toBe(false)
  })
})

describe('review polish helpers', () => {
  it('names each Bulk control after its check', () => {
    expect(new Set(['Fix names', 'Fix typos'].map(bulkControlName)).size).toBe(2)
    expect(bulkControlName('Fix names')).toBe('Bulk: Fix names')
  })
  it('does not double the full stop in the split announcement', () => {
    expect(splitAnnouncement('Hello there.', 'Bye。')).toBe('First piece: Hello there. Second piece: Bye.')
    expect(splitAnnouncement('Hello', 'Bye')).toBe('First piece: Hello. Second piece: Bye.')
  })
  it('puts the caret at the end when the translation box opens', () => {
    const calls: number[][] = []
    caretToEnd({ value: 'abc', setSelectionRange: (a: number, b: number) => calls.push([a, b]) } as never)
    caretToEnd(null)
    expect(calls).toEqual([[3, 3]])
  })
})
