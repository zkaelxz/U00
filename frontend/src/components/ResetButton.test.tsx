import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { ResetButton } from './ResetButton'

const noop = vi.fn()

describe('ResetButton', () => {
  it('offers the reset first', () => {
    const html = renderToStaticMarkup(<ResetButton offered={false} onReset={noop} onUndo={noop} />)
    expect(html).toContain('Reset to defaults')
    expect(html).not.toContain('Undo')
  })

  it('turns into "Reset. Undo" once the reset ran', () => {
    const html = renderToStaticMarkup(<ResetButton offered onReset={noop} onUndo={noop} />)
    expect(html).toContain('Reset.')
    expect(html).toContain('Undo')
    expect(html).not.toContain('Reset to defaults')
  })
})
