import { expect, it, vi } from 'vitest'

import { revealForm } from './revealForm'

it('scrolls the form into view and focuses its first input', () => {
  const focus = vi.fn()
  const scrollIntoView = vi.fn()
  const form = { scrollIntoView, querySelector: vi.fn(() => ({ focus })) } as unknown as HTMLElement
  revealForm(form)
  expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center' })
  expect(focus).toHaveBeenCalledWith({ preventScroll: true })
})

it('does nothing without a form', () => {
  expect(() => revealForm(null)).not.toThrow()
})
