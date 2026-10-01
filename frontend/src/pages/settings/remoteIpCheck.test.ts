import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import { clearIpCheck, getIpCheckStatus, setIpCheck, testIpCheck } from '../../api/diagnostics'
import { getPcMode, resetPcModeForTests } from '../../api/pcOnly'
import { draftProblem, ipCheckErrorMessage, testBadge } from './remoteIpCheck'
import { KEY_WRITES_REFUSED } from '../../components/errorMessages'

const SECRET = 'https://ip.example.net/?token=SECRET-DDNS-TOKEN'

function fakeFetch(status: number, body: unknown) {
  return vi.fn(async (..._args: Parameters<typeof fetch>) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  )
}

afterEach(() => resetPcModeForTests())

describe('remote access: public-address check', () => {
  it('checks the draft before sending', () => {
    expect(draftProblem('')).toBeNull()
    expect(draftProblem(' https://api.ipify.org ')).toBeNull()
    expect(draftProblem(SECRET)).toBeNull()
    expect(draftProblem('http://api.ipify.org')).toMatch(/https:\/\//)
    expect(draftProblem('https://user@api.ipify.org')).toMatch(/https:\/\//)
    expect(draftProblem('https://api.ipify.org/a b')).toMatch(/no spaces/)
    expect(draftProblem(`https://api.ipify.org/${'a'.repeat(600)}`)).toBe('The address is too long.')
  })

  it('labels a Test result', () => {
    expect(testBadge({ state: 'ok' })).toEqual({ tone: 'ok', label: 'OK' })
    expect(testBadge({ state: 'warn' }).tone).toBe('warn')
    expect(testBadge({ state: 'unknown' }).label).toBe('Could not tell')
  })

  it('explains errors without echoing anything', () => {
    const refused = new ApiError(403, { code: 'forbidden', message: 'Not allowed from this connection.' })
    expect(ipCheckErrorMessage(refused)).toBe(KEY_WRITES_REFUSED)
    expect(ipCheckErrorMessage(refused, false)).toBe('This only works on the main PC.')
    expect(ipCheckErrorMessage(new ApiError(429, { code: 'rate_limited', message: 'x' }), false)).toMatch(/few seconds/)
    expect(ipCheckErrorMessage(new ApiError(422, { code: 'invalid_input', message: 'The address is too long.' })))
      .toBe('The address is too long.')
    expect(ipCheckErrorMessage(new Error('boom'))).toBe('That did not work. Try again.')
  })

  it('sends the address in the body only; Test is PC only', async () => {
    const f = fakeFetch(200, { configured: true })
    await expect(setIpCheck(SECRET, f)).resolves.toEqual({ configured: true })
    const [url, init] = f.mock.calls[0]
    expect(String(url)).toBe('/api/diagnostics/remote-health/ip-check')
    expect(String(url)).not.toContain('SECRET')
    expect(JSON.parse(String(init?.body))).toEqual({ value: SECRET, confirm: true })

    const c = fakeFetch(200, { configured: false })
    await clearIpCheck(c)
    expect(String(c.mock.calls[0][0])).toBe('/api/diagnostics/remote-health/ip-check/clear')
    const g = fakeFetch(200, { configured: false })
    await expect(getIpCheckStatus(g)).resolves.toEqual({ configured: false })

    const t = fakeFetch(403, { error: { code: 'forbidden', message: 'Not allowed.' } })
    await expect(testIpCheck(t)).rejects.toBeInstanceOf(ApiError)
    expect(new Headers(t.mock.calls[0][1]?.headers).get('X-Baihe-Local')).toBe('1')
    expect(getPcMode()).toBe('remote')
  })
})
