import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import { listNotifications, setNotificationCategories } from './notifications'
import { getPcMode, resetPcModeForTests } from './pcOnly'

const STATUS = {
  discord_configured: true,
  ntfy_configured: false,
  ntfy_allow_local: false,
  send_jobs: false,
  send_chapters: true,
}

const reply = (body: unknown, status = 200) =>
  vi.fn(async (..._args: Parameters<typeof fetch>) =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }),
  )

afterEach(() => resetPcModeForTests())

describe('setNotificationCategories', () => {
  it('POSTs only the changed switch, PC-only, and returns the status', async () => {
    const f = reply(STATUS)
    expect(await setNotificationCategories({ jobs: false }, f)).toEqual(STATUS)
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/settings/notifications/categories')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({ jobs: false })
    const headers = new Headers(init?.headers)
    expect(headers.get('X-Baihe-Local')).toBe('1')
    expect(headers.get('Content-Type')).toBe('application/json')
  })

  it('a 403 marks the tab as away from the PC', async () => {
    resetPcModeForTests('local')
    const refused = reply({ error: { code: 'forbidden', message: 'Not allowed.' } }, 403)
    await expect(setNotificationCategories({ chapters: true }, refused)).rejects.toBeInstanceOf(ApiError)
    expect(getPcMode()).toBe('remote')
  })
})

describe('listNotifications', () => {
  it('is a plain GET of /api/notifications', async () => {
    const items = [{ id: 2, at: 1_790_000_000, kind: 'job_failed', text: 'Translate failed: Signal' }]
    const f = reply({ items })
    expect(await listNotifications(f)).toEqual({ items })
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/notifications')
    expect(init?.method).toBeUndefined()
  })

  it('a refusal is an ApiError and does not touch the PC mode', async () => {
    resetPcModeForTests('local')
    const f = reply({ error: { code: 'forbidden', message: 'Not allowed.' } }, 403)
    await expect(listNotifications(f)).rejects.toMatchObject({ status: 403 })
    expect(getPcMode()).toBe('local')
  })
})
