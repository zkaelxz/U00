import { describe, expect, it, vi } from 'vitest'

import { ApiError } from './client'
import {
  clearNotionToken, getNotionConfig, getNotionDramaPage, saveNotionConfig, setNotionToken, startNotionExport, testNotion,
} from './notion'

const cfg = { target_type: 'page', target_id: '01234567-89ab-cdef-0123-456789abcdef', token_configured: true }
const ok = (body: unknown, status = 200) =>
  vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch
const call = (f: typeof fetch, i = 0) => {
  const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[i]
  return { url, init, body: init?.body ? JSON.parse(init.body) : undefined, headers: new Headers(init?.headers) }
}

describe('notion api', () => {
  it('reads the config as a PC-only call', async () => {
    const f = ok(cfg)
    expect(await getNotionConfig(f)).toEqual(cfg)
    const c = call(f)
    expect(c.url).toBe('/api/notion/config')
    expect(c.headers.get('X-Baihe-Local')).toBe('1')
  })

  it('posts only the given config fields', async () => {
    const f = ok(cfg)
    await saveNotionConfig({ target_id: '' }, f)
    const c = call(f)
    expect(c.url).toBe('/api/notion/config')
    expect(c.init.method).toBe('POST')
    expect(c.body).toEqual({ target_id: '' })
  })

  it('sends the token in the body with confirm, and clears with confirm', async () => {
    const f = ok(cfg)
    await setNotionToken('ntn_secret', f)
    expect(call(f).url).toBe('/api/notion/token')
    expect(call(f).body).toEqual({ value: 'ntn_secret', confirm: true })
    expect(call(f).url).not.toContain('ntn_secret')
    const g = ok({ ...cfg, token_configured: false })
    expect((await clearNotionToken(g)).token_configured).toBe(false)
    expect(call(g).url).toBe('/api/notion/token/clear')
    expect(call(g).body).toEqual({ confirm: true })
  })

  it('tests, reads the drama page and starts an export', async () => {
    const t = ok({ ok: true, bot_name: 'B', target_title: 'T', target_type: 'page' })
    expect((await testNotion(t)).bot_name).toBe('B')
    expect(call(t).body).toEqual({})
    const p = ok({ drama_id: 3, page_id: null, page_url: null })
    expect(await getNotionDramaPage(3, p)).toEqual({ drama_id: 3, page_id: null, page_url: null })
    expect(call(p).url).toBe('/api/notion/dramas/3')
    const e = ok({ job_id: 'notion_export_3' })
    expect(await startNotionExport(3, 'bilingual', e)).toEqual({ job_id: 'notion_export_3' })
    expect(call(e).url).toBe('/api/notion/dramas/3/export')
    expect(call(e).body).toEqual({ field: 'bilingual' })
  })

  it('surfaces a 409 as an ApiError with the server message', async () => {
    const f = ok({ error: { code: 'conflict', message: 'An export is already running.' } }, 409)
    const err = await startNotionExport(1, 'en', f).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe('An export is already running.')
  })
})
