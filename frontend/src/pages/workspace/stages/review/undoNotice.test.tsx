import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { UndoNotice } from './UndoNotice'

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
    const html = renderToStaticMarkup(<UndoNotice message="Line #3 is now Anok." busy={false} historyNote={false} onUndo={() => {}} onDismiss={() => {}} />)
    expect(html).toContain('Line #3 is now Anok.')
    expect(html).not.toContain('Records → Line history')
  })
})
