import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { errorAction } from './errorActions'
import { describeError, MISSING_PACKAGE_TITLE } from './errorMessages'

const dep = (details: Record<string, unknown>, message = 'x') => new ApiError(503, { code: 'dependency_unavailable', message, details })

describe('the error heading follows the cause', () => {
  it('a missing Hugging Face token is a token heading with a Settings action, not "not installed"', () => {
    const err = dep({ reason: 'no_token', engine: 'Hugging Face' }, 'No Hugging Face token is configured. Add it in Settings.')
    const { title, detail } = describeError(err)
    expect(title).toBe('No Hugging Face token is set. Add it in Settings.')
    expect(title).not.toMatch(/not installed/)
    expect(detail).toBeNull()
    expect(errorAction(err)).toEqual({ label: 'Open Settings', href: '#/settings' })
  })

  it('a missing key keeps its heading and action', () => {
    const err = dep({ reason: 'no_key', engine: 'groq' })
    expect(describeError(err).title).toBe('No key is set for Groq. Add it in Settings.')
    expect(errorAction(err)?.href).toBe('#/settings')
  })

  it('a missing package says install, with the package picked in Diagnostics', () => {
    const err = dep({ reason: 'missing_package', package: 'paddleocr' }, 'The paddle OCR backend needs paddleocr. Install it in Diagnostics.')
    expect(describeError(err).title).toBe(MISSING_PACKAGE_TITLE)
    expect(errorAction(err)).toEqual({ label: 'Open Diagnostics', href: '#/diagnostics?card=packages&package=paddleocr' })
  })

  it('never builds a link from odd text', () => {
    expect(errorAction(dep({ reason: 'missing_package', package: '../../etc' }))?.href).toBe('#/diagnostics?card=packages')
    expect(errorAction(dep({ reason: 'missing_package', package: { x: 1 } }))?.href).toBe('#/diagnostics?card=packages')
  })

  it('has no action for an error without a known cause', () => {
    expect(errorAction(dep({}))).toBeNull()
    expect(errorAction(new ApiError(500, { code: 'internal_error', message: 'x' }))).toBeNull()
  })
})
