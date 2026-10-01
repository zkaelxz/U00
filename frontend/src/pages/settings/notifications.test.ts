import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import {
  clearNotificationChannel,
  getNotificationStatus,
  sendTestNotification,
  setNotificationChannel,
} from '../../api/notifications'
import { getPcMode, resetPcModeForTests } from '../../api/pcOnly'
import {
  CATEGORIES,
  categoryChange,
  notificationErrorMessage,
  notificationSummary,
  testResultText,
} from './notifications'
import { KEY_WRITES_REFUSED } from '../../components/errorMessages'

const SECRET = 'https://discord.com/api/webhooks/123456789012345678/SECRETtokenABCDEFGHIJKLMNOP'

function fakeFetch(status: number, body: unknown) {
  return vi.fn(async (..._args: Parameters<typeof fetch>) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  )
}

afterEach(() => resetPcModeForTests())

describe('notification text helpers', () => {
  it('summarises which channels are on', () => {
    expect(notificationSummary({ discord_configured: false, ntfy_configured: false, ntfy_allow_local: false, send_jobs: true, send_chapters: true, send_remote: true })).toBe('Off')
    expect(
      notificationSummary({ discord_configured: true, ntfy_configured: true, ntfy_allow_local: false, send_jobs: false, send_chapters: false, send_remote: false }),
    ).toBe(
      'On: Discord, ntfy',
    )
  })

  it('maps errors to one plain line', () => {
    expect(notificationErrorMessage(new ApiError(403, { code: 'forbidden', message: 'Not allowed.' }))).toBe(
      KEY_WRITES_REFUSED,
    )
    expect(notificationErrorMessage(new ApiError(429, { code: 'rate_limited', message: 'x' }))).toMatch(/Too many/)
    expect(
      notificationErrorMessage(new ApiError(422, { code: 'invalid_input', message: 'That is not a Discord webhook address.' })),
    ).toBe('That is not a Discord webhook address.')
    expect(notificationErrorMessage(new Error('boom'))).toBe('That did not work. Try again.')
  })

  it('a category switch sends only its own field', () => {
    expect(categoryChange('send_jobs', false)).toEqual({ jobs: false })
    expect(categoryChange('send_chapters', true)).toEqual({ chapters: true })
    expect(categoryChange('send_remote', false)).toEqual({ remote: false })
    expect(CATEGORIES.map((c) => c.label)).toEqual(['Jobs finished or failed', 'New chapters found', 'Remote access problems'])
  })

  it('describes test results, skipping channels that are not set up', () => {
    expect(testResultText({ results: { discord: 'sent', ntfy: 'not_configured' } })).toBe('Discord: sent.')
    expect(testResultText({ results: { discord: 'failed', ntfy: 'refused' } })).toMatch(
      /^Discord: could not be sent .*\. ntfy: refused, the address is not allowed\.$/,
    )
  })
})

describe('notification API calls', () => {
  it('reads status with a plain GET', async () => {
    const f = fakeFetch(200, { discord_configured: true, ntfy_configured: false, ntfy_allow_local: false })
    expect((await getNotificationStatus(f)).discord_configured).toBe(true)
    expect(f.mock.calls[0][0]).toBe('/api/settings/notifications')
  })

  it('sends the address in the body only, as JSON with confirm', async () => {
    const f = fakeFetch(200, { channel: 'discord', configured: true })
    await setNotificationChannel('discord', SECRET, f)
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/settings/notifications/discord')
    expect(String(url)).not.toContain('SECRET')
    expect(JSON.parse(String(init?.body))).toEqual({ value: SECRET, confirm: true })
    expect(new Headers(init?.headers).get('Content-Type')).toBe('application/json')
  })

  it('a refused save (key writes off) leaves the tab local', async () => {
    resetPcModeForTests('local')
    const refused = fakeFetch(403, { error: { code: 'forbidden', message: 'Not allowed.' } })
    await expect(setNotificationChannel('ntfy', SECRET, refused)).rejects.toBeInstanceOf(ApiError)
    await expect(clearNotificationChannel('ntfy', refused)).rejects.toBeInstanceOf(ApiError)
    expect(getPcMode()).toBe('local')
  })

  it('clear is a POST with confirm; a 403 on send test marks the tab remote', async () => {
    const ok = fakeFetch(200, { channel: 'ntfy', configured: false })
    await clearNotificationChannel('ntfy', ok)
    expect(ok.mock.calls[0][0]).toBe('/api/settings/notifications/ntfy/clear')
    expect(JSON.parse(String(ok.mock.calls[0][1]?.body))).toEqual({ confirm: true })

    const refused = fakeFetch(403, { error: { code: 'forbidden', message: 'Not allowed.' } })
    await expect(sendTestNotification(refused)).rejects.toBeInstanceOf(ApiError)
    expect(refused.mock.calls[0][0]).toBe('/api/settings/notifications/test')
    expect(getPcMode()).toBe('remote')
  })
})
