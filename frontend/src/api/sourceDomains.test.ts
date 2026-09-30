import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { resetPcModeForTests } from './pcOnly'
import {
  confirmDomainProposal,
  dismissDomainProposal,
  isMissingRoute,
  listSourceDomains,
  resetSourceDomains,
  saveSourceDomains,
} from './sourceDomains'

function reply(status: number, body: unknown) {
  const mock = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  return { mock, f: mock as unknown as typeof fetch }
}
const header = (init: RequestInit, k: string) => new Headers(init.headers).get(k)

afterEach(() => resetPcModeForTests())

describe('source domains api', () => {
  it('lists with GET', async () => {
    const { mock, f } = reply(200, [])
    await listSourceDomains(f)
    expect(mock.mock.calls[0][0]).toBe('/api/source-domains')
  })

  it('saves only the domains', async () => {
    const { mock, f } = reply(200, {})
    await saveSourceDomains('a b', ['x.example'], f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/source-domains/a%20b')
    expect(JSON.parse(init.body)).toEqual({ domains: ['x.example'] })
    expect(header(init, 'X-Baihe-Local')).toBe('1')
  })

  it('reset sends the local header and a JSON content type', async () => {
    const { mock, f } = reply(200, {})
    await resetSourceDomains('a', f)
    const [url, init] = mock.mock.calls[0]
    expect(url).toBe('/api/source-domains/a/reset')
    expect(init.method).toBe('POST')
    expect(header(init, 'X-Baihe-Local')).toBe('1')
    expect(header(init, 'Content-Type')).toBe('application/json')
  })

  it('confirm and dismiss post source and host', async () => {
    const { mock, f } = reply(200, { dismissed: true })
    await confirmDomainProposal('a', 'n.example', f)
    await dismissDomainProposal('a', 'n.example', f)
    expect(mock.mock.calls[0][0]).toBe('/api/source-domains/proposals/confirm')
    expect(mock.mock.calls[1][0]).toBe('/api/source-domains/proposals/dismiss')
    expect(JSON.parse(mock.mock.calls[1][1].body)).toEqual({ source: 'a', host: 'n.example' })
  })

  it('tells a missing route from other errors', async () => {
    const { f } = reply(404, { error: { code: 'not_found', message: 'x' } })
    const err = await listSourceDomains(f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(isMissingRoute(err)).toBe(true)
    expect(isMissingRoute(new ApiError(422, { code: 'validation_error', message: 'x' }))).toBe(false)
  })
})
