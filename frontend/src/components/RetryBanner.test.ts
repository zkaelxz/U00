import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { RetryBanner } from './RetryBanner'

const render = (props: Parameters<typeof RetryBanner>[0]) => renderToStaticMarkup(createElement(RetryBanner, props))

describe('RetryBanner', () => {
  it('renders nothing without an error', () => {
    expect(render({ error: null, onRetry: () => {} })).toBe('')
  })

  it('names what could not load, shows the reason and offers Retry', () => {
    const err = new ApiError(500, { code: 'internal_error', message: 'x' }, 'GET')
    const html = render({ error: err, onRetry: () => {}, what: 'the title' })
    expect(html).toContain('role="alert"')
    expect(html).toContain('Retry')
    expect(html).toContain('Could not load')
  })

  it('escapes error text instead of rendering it as HTML', () => {
    const err = new ApiError(409, { code: 'conflict', message: '<img src=x onerror=alert(1)>' }, 'GET')
    expect(render({ error: err, onRetry: () => {} })).not.toContain('<img')
  })

  it('disables Retry while a retry is running', () => {
    const err = new ApiError(500, { code: 'internal_error', message: 'x' }, 'GET')
    expect(render({ error: err, onRetry: () => {}, busy: true })).toContain('disabled')
  })
})
