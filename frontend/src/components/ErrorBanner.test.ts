import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { ErrorBanner } from './ErrorBanner'
import { describeError } from './errorMessages'

const err = (code: string, message = 'm') => new ApiError(400, { code, message })

describe('describeError', () => {
  it.each([
    'validation_error', 'not_found', 'conflict', 'unsupported_operation',
    'dependency_unavailable', 'application_error', 'internal_error', 'network_error',
  ])('has plain text for %s', (code) => {
    const { title } = describeError(err(code))
    expect(title.length).toBeGreaterThan(10)
    expect(title).not.toContain(code)
  })

  it('falls back for an unknown code', () => {
    expect(describeError(err('weird')).title).toContain('failed')
    expect(describeError(null).title).toContain('Something went wrong')
  })

  it('shows safe server text but never paths or keys', () => {
    expect(describeError(err('conflict', 'A job is already running.')).detail).toBe('A job is already running.')
    expect(describeError(err('not_found', 'Missing /home/kae/library/x.srt')).detail).toBeNull()
    expect(describeError(err('conflict', 'C:\\Users\\kae\\a.srt')).detail).toBeNull()
    expect(describeError(err('dependency_unavailable', 'api_key=sk-abcdef123456')).detail).toBeNull()
    // Codes whose server text is not shown at all.
    expect(describeError(err('internal_error', 'safe words')).detail).toBeNull()
  })
})

describe('ErrorBanner', () => {
  it('renders an alert, and nothing without an error', () => {
    const html = renderToStaticMarkup(createElement(ErrorBanner, { error: err('not_found', 'No title with id 9.') }))
    expect(html).toContain('role="alert"')
    expect(html).toContain('No title with id 9.')
    expect(renderToStaticMarkup(createElement(ErrorBanner, { error: null }))).toBe('')
  })
})

describe('extension_only', () => {
  const e = err('extension_only', 'This site only works through the browser extension.')

  it('says it plainly', () => {
    expect(describeError(e).title).toContain('only works through the browser extension')
  })

  it('links to the extension help', () => {
    const html = renderToStaticMarkup(createElement(ErrorBanner, { error: e }))
    expect(html).toContain('Extension help')
    expect(html).toContain('href="#/settings"')
    expect(renderToStaticMarkup(createElement(ErrorBanner, { error: err('conflict') }))).not.toContain('Extension help')
  })
})
