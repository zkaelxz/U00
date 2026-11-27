import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { DisabledReason } from './DisabledReason'

describe('DisabledReason', () => {
  it('shows the reason as visible text a button can point at', () => {
    const html = renderToStaticMarkup(<DisabledReason id="r">Choose a voice first.</DisabledReason>)
    expect(html).toContain('id="r"')
    expect(html).toContain('Choose a voice first.')
  })

  it('renders nothing once the button is no longer disabled for it', () => {
    expect(renderToStaticMarkup(<DisabledReason id="r">{null}</DisabledReason>)).toBe('')
  })
})
