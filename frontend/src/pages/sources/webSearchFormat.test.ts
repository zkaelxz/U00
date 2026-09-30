import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import { ADDRESS_WRITES_REFUSED, webResultsHeader, webSearchErrorMessage, webSearchSummary } from './webSearchFormat'

describe('web search format', () => {
  it('summarises the setting', () => {
    expect(webSearchSummary({ enabled: false, base_url: 'http://x' })).toBe('Off')
    expect(webSearchSummary({ enabled: true, base_url: null })).toBe('On, no address')
    expect(webSearchSummary({ enabled: true, base_url: 'http://x' })).toBe('On')
  })

  it('counts results', () => {
    expect(webResultsHeader(0)).toBe('No web results.')
    expect(webResultsHeader(1)).toBe('1 web result')
    expect(webResultsHeader(20)).toBe('20 web results')
  })

  it('shows fixed server text and explains a 403', () => {
    expect(webSearchErrorMessage(new ApiError(503, { code: 'SERVICE_UNAVAILABLE', message: 'Couldn\'t reach the SearXNG server.' }))).toBe(
      "Couldn't reach the SearXNG server.",
    )
    expect(webSearchErrorMessage(new ApiError(403, { code: 'FORBIDDEN', message: 'no' }), true)).toBe(ADDRESS_WRITES_REFUSED)
    expect(webSearchErrorMessage(new ApiError(403, { code: 'FORBIDDEN', message: 'no' }))).toMatch(/PC only/)
    expect(webSearchErrorMessage(new ApiError(500, { code: 'INTERNAL', message: 'trace at /home/x' }))).toBe('That did not work. Try again.')
    expect(webSearchErrorMessage(new Error('x'))).toBe('That did not work. Try again.')
  })
})
